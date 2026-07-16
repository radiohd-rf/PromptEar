"""PromptEar — главное окно приложения."""

import contextlib
import importlib
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from config import (
    COLORS,
    FONT_FAMILY,
    FONT_SIZE,
    FONT_SIZE_SMALL,
    OUTPUT_FORMATS,
    QUEUE_POLL_MS,
    SPINNER_FRAMES,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from processing.detector import detect_quiet_audio, preprocess_audio
from processing.enhancer import OllamaEnhancer
from processing.transcriber import Transcriber
from ui.widgets import PlaceholderEntry, PlaceholderListbox
from utils.files import find_audio_files, save_docx, save_txt
from utils.protocol import QueueMsg

WHITE = COLORS["white"]
BORDER = COLORS["border"]
FG = COLORS["fg"]
PH = COLORS["ph"]
BTN_ACTIVE_BG = COLORS["btn_active_bg"]

# Проверка tkinterdnd2
HAS_DND = importlib.util.find_spec("tkinterdnd2") is not None


class PromptEarApp:
    """Главное окно приложения."""

    def __init__(self, root):
        self.root = root
        self.root.title("PromptEar — транскрибация аудио")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.resizable(False, False)

        self._queue: queue.Queue = queue.Queue()
        self._check_queue()

        self._transcriber = Transcriber()
        self._enhancer = OllamaEnhancer()
        self._ffmpeg_ok = shutil.which("ffmpeg") is not None
        self._cancel = False
        self._running = False

        self._spinner_frames = SPINNER_FRAMES
        self._spinner_index = 0
        self._spinner_running = False
        self._spinner_job = None

        self._build_ui()

        from utils.gpu import detect_and_report

        gpu_info = detect_and_report()
        self._log(
            f"GPU: nvidia={gpu_info['has_nvidia_gpu']}, "
            f"torch_cuda={gpu_info['torch_cuda_installed']}, "
            f"device={gpu_info['device']}"
        )
        if gpu_info["need_install"]:
            self._log("Обнаружена NVIDIA, но torch без CUDA")
            if messagebox.askyesno(
                "GPU обнаружена",
                "Обнаружена видеокарта NVIDIA.\n\n"
                "Установить torch с CUDA для ускорения?\n"
                "(Займёт ~2 минуты)",
            ):
                self._install_cuda()
            else:
                self._log("Установка CUDA отменена, работаю на CPU")
        elif gpu_info["has_nvidia_gpu"] and gpu_info["torch_cuda_installed"]:
            self._log("GPU: NVIDIA + CUDA готовы")
        else:
            self._log("GPU: не обнаружена (CPU)")

        if HAS_DND:
            self.root.drop_target_register("DND_Files")
            self.root.dnd_bind("<<Drop>>", self._on_drop)

        self._process_cli_args()
        self._check_ollama_async()

    # ------------------------------------------------------------------
    def _build_ui(self):
        """Строит интерфейс."""
        self.root.configure(bg=WHITE)
        self.root.option_add("*Font", (FONT_FAMILY, FONT_SIZE))
        self.root.option_add("*HighlightThickness", 0)

        main = tk.Frame(self.root, bg=WHITE)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Path + Browse ---
        row_path = tk.Frame(main, bg=WHITE)
        row_path.pack(fill=tk.X, pady=(0, 6))

        self._entry_path = PlaceholderEntry(
            row_path,
            placeholder='выберите папку с аудио: нажмите "Обзор"',
            bg=WHITE,
            fg=PH,
            normal_fg=FG,
            insertbackground=FG,
            relief=tk.SOLID,
            bd=1,
            font=(FONT_FAMILY, FONT_SIZE),
        )
        self._entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_browse = tk.Button(
            row_path,
            text="Обзор",
            command=self._on_browse,
            bg=WHITE,
            fg=FG,
            activebackground=BTN_ACTIVE_BG,
            activeforeground=FG,
            font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.SOLID,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=4,
            cursor="hand2",
        )
        btn_browse.pack(side=tk.RIGHT)

        # --- File list ---
        frm_list = tk.Frame(main, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        frm_list.pack(fill=tk.X, pady=(0, 6))

        self._listbox = PlaceholderListbox(
            frm_list,
            placeholder="перетащите файлы сюда",
            height=4,
            selectmode=tk.EXTENDED,
            font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.FLAT,
            highlightthickness=0,
            bg=WHITE,
            fg=PH,
            normal_fg=FG,
            selectbackground="#eee",
            selectforeground=FG,
            borderwidth=0,
        )
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_files = tk.Scrollbar(
            frm_list,
            orient=tk.VERTICAL,
            command=self._listbox.yview,
            bg=WHITE,
            troughcolor=WHITE,
            bd=1,
            relief=tk.SOLID,
        )
        scroll_files.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.config(yscrollcommand=scroll_files.set)

        self._listbox.bind("<Delete>", self._remove_selected)
        if HAS_DND:
            self._listbox.drop_target_register("DND_Files")  # type: ignore[attr-defined]
            self._listbox.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
        self._listbox.bind("<Button-3>", self._show_list_menu)

        # --- Context ---
        frm_ctx = tk.Frame(main, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        frm_ctx.pack(fill=tk.X, pady=(0, 4))

        self._text_prompt = tk.Text(
            frm_ctx,
            height=2,
            wrap=tk.WORD,
            font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.FLAT,
            highlightthickness=0,
            bg=WHITE,
            fg=PH,
            borderwidth=0,
            insertbackground=FG,
        )
        self._text_prompt.insert("1.0", "контекст (тема, имена, термины)")
        self._text_prompt.pack(fill=tk.X)
        self._text_prompt.bind("<FocusIn>", self._on_prompt_focus)
        self._text_prompt.bind("<FocusOut>", self._on_prompt_blur)
        self._text_prompt.bind("<Button-3>", self._show_text_menu)

        # --- Format + multi-pass + button ---
        row_fmt = tk.Frame(main, bg=WHITE)
        row_fmt.pack(fill=tk.X, pady=(0, 4))

        self._var_format = tk.StringVar(value="docx")
        for fmt in OUTPUT_FORMATS:
            rb = tk.Radiobutton(
                row_fmt,
                text=fmt.upper(),
                variable=self._var_format,
                value=fmt,
                bg=WHITE,
                fg=FG,
                selectcolor=WHITE,
                activebackground=WHITE,
                activeforeground=FG,
                font=(FONT_FAMILY, FONT_SIZE),
            )
            rb.pack(side=tk.LEFT, padx=(0, 10))

        self._var_multi_pass = tk.BooleanVar(value=False)
        cb_multipass = tk.Checkbutton(
            row_fmt,
            text="Многопроходное улучшение текста",
            variable=self._var_multi_pass,
            bg=WHITE,
            fg=FG,
            selectcolor=WHITE,
            activebackground=WHITE,
            activeforeground=FG,
            font=(FONT_FAMILY, FONT_SIZE),
        )
        cb_multipass.pack(side=tk.LEFT, padx=(20, 0))

        self._btn_run = tk.Button(
            row_fmt,
            text="Обработать",
            command=self._on_run,
            bg=WHITE,
            fg=FG,
            activebackground=BTN_ACTIVE_BG,
            activeforeground=FG,
            font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.SOLID,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self._btn_run.pack(side=tk.RIGHT)

        # --- Progress ---
        row_progress = tk.Frame(main, bg=WHITE)
        row_progress.pack(fill=tk.X, pady=(2, 4))

        self._spinner_label = tk.Label(
            row_progress,
            text="",
            anchor=tk.W,
            bg=WHITE,
            fg=COLORS["spinner"],
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
        )
        self._spinner_label.pack(side=tk.LEFT)

        self._status_label = tk.Label(
            row_progress,
            text="",
            anchor=tk.E,
            bg=WHITE,
            fg=COLORS["status"],
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
        )
        self._status_label.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        # --- Log ---
        frm_log = tk.Frame(main, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        frm_log.pack(fill=tk.BOTH, expand=True)

        self._text_log = tk.Text(
            frm_log,
            height=5,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
            relief=tk.FLAT,
            highlightthickness=0,
            bg=WHITE,
            fg=FG,
            borderwidth=0,
            insertbackground=FG,
        )
        self._text_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_log = tk.Scrollbar(
            frm_log,
            orient=tk.VERTICAL,
            command=self._text_log.yview,
            bg=WHITE,
            troughcolor=WHITE,
            bd=1,
            relief=tk.SOLID,
        )
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self._text_log.config(yscrollcommand=scroll_log.set)

    # ------------------------------------------------------------------
    def _log(self, message: str):
        """Добавляет сообщение в лог."""
        self._text_log.config(state=tk.NORMAL)
        self._text_log.insert(tk.END, message + "\n")
        self._text_log.see(tk.END)
        self._text_log.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    def _start_spinner(self, text=""):
        self._spinner_running = True
        self._status_label.config(text=text)
        self._animate_spinner()

    def _stop_spinner(self):
        self._spinner_running = False
        if self._spinner_job:
            self.root.after_cancel(self._spinner_job)
            self._spinner_job = None
        self._spinner_label.config(text="")
        self._status_label.config(text="")

    def _animate_spinner(self):
        if not self._spinner_running:
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        self._spinner_label.config(text=self._spinner_frames[self._spinner_index])
        self._spinner_job = self.root.after(120, self._animate_spinner)

    def _set_progress_text(self, text: str):
        """Обновляет текст статуса (не останавливая анимацию)."""
        self._status_label.config(text=text)

    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool, label: str = ""):
        """Переключает состояние интерфейса: занят / свободен."""
        state = tk.DISABLED if busy else tk.NORMAL
        self._btn_run.config(state=state)  # type: ignore[call-overload]
        if busy:
            self._start_spinner(label)
        else:
            self._stop_spinner()

    # ------------------------------------------------------------------
    # Обработчики UI
    # ------------------------------------------------------------------
    def _on_browse(self):
        """Обработчик кнопки 'Обзор папки'."""
        folder = filedialog.askdirectory(title="Выберите папку с аудиофайлами")
        if not folder:
            return
        self._entry_path.set_text(folder)
        self._add_files([folder])

    def _on_stop(self):
        """Остановка обработки."""
        self._cancel = True
        self._btn_run.config(text="Остановка...", state=tk.DISABLED)
        self._log("Остановка после текущего файла...")

    def _on_drop(self, event):
        """Обработчик drag-and-drop."""
        raw = event.data
        if not raw:
            return
        files = []
        for part in raw.split():
            part = part.strip("{}")
            if os.path.exists(part):
                files.append(part)
        self._add_files(files)

    def _remove_selected(self, event=None):
        """Удаляет выделенные файлы из списка."""
        selected = self._listbox.curselection()
        for i in reversed(selected):
            self._listbox.delete(i)
        self._listbox._show_placeholder()

    def _show_list_menu(self, event):
        """Контекстное меню для списка файлов."""
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, FONT_SIZE))
        menu.add_command(label="Удалить", command=self._remove_selected)
        menu.add_command(label="Очистить всё", command=self._listbox.clear)
        menu.tk_popup(event.x_root, event.y_root)

    def _show_text_menu(self, event):
        """Контекстное меню для поля контекста."""
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, FONT_SIZE))
        menu.add_command(label="Копировать", command=self._copy_text)
        menu.add_command(label="Вырезать", command=self._cut_text)
        menu.add_command(label="Вставить", command=self._paste_text)
        menu.tk_popup(event.x_root, event.y_root)

    def _copy_text(self):
        """Копирует выделенный текст."""
        try:
            text = self._text_prompt.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _cut_text(self):
        """Вырезает выделенный текст."""
        self._copy_text()
        with contextlib.suppress(tk.TclError):
            self._text_prompt.delete("sel.first", "sel.last")

    def _paste_text(self):
        """Вставляет текст из буфера."""
        try:
            text = self.root.clipboard_get()
            self._text_prompt.insert("insert", text)
        except tk.TclError:
            pass

    def _on_prompt_focus(self, event):
        """Убирает плейсхолдер при фокусе."""
        if self._text_prompt.get("1.0", "end-1c") == "контекст (тема, имена, термины)":
            self._text_prompt.delete("1.0", tk.END)
            self._text_prompt.config(fg=FG)

    def _on_prompt_blur(self, event):
        """Возвращает плейсхолдер, если поле пустое."""
        if not self._text_prompt.get("1.0", "end-1c").strip():
            self._text_prompt.delete("1.0", tk.END)
            self._text_prompt.insert("1.0", "контекст (тема, имена, термины)")
            self._text_prompt.config(fg=PH)

    # ------------------------------------------------------------------
    # Работа с файлами
    # ------------------------------------------------------------------
    def _add_files(self, paths):
        """Добавляет пути в список для обработки."""
        files = find_audio_files(paths)
        if not files:
            self._log("Не найдено аудиофайлов в указанных путях")
            return

        self._listbox.clear()
        for f in files:
            self._listbox.add_file(str(f))
        self._log(f"Добавлено {len(files)} файлов")

        for p in paths:
            if os.path.isdir(p):
                norm = os.path.normpath(p)
                self._entry_path.set_text(norm)
                break

    def _process_cli_args(self):
        """Обрабатывает файлы, переданные через аргументы командной строки."""
        args = sys.argv[1:]
        if not args:
            return
        files = [a for a in args if not a.startswith("-")]
        if files:
            self._add_files(files)

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------
    def _check_ollama_async(self):
        """Проверяет доступность Ollama и модели Qwen в фоне."""

        def check():
            ok, model = self._enhancer.is_available()
            if ok and model:
                self._queue.put((QueueMsg.OLLAMA_READY, (True, True)))
            elif ok and not model:
                self._queue.put((QueueMsg.OLLAMA_READY, (True, False)))
            else:
                self._queue.put((QueueMsg.OLLAMA_READY, (False, False)))

        threading.Thread(target=check, daemon=True).start()

    def _install_ollama(self):
        """Скачивает и устанавливает Ollama."""
        self._log("Скачивание Ollama...")
        self._set_busy(True, "Установка Ollama...")

        def install():
            try:

                def on_progress(msg):
                    self._queue.put((QueueMsg.LOG, msg))

                self._enhancer.install(progress_callback=on_progress)
                self._queue.put((QueueMsg.OLLAMA_READY, (True, True)))
            except Exception as exc:
                self._queue.put((QueueMsg.LOG, f"Ошибка установки Ollama: {exc}"))
                self._queue.put((QueueMsg.LOG, "  Попробуйте: winget install Ollama"))
                self._queue.put((QueueMsg.OLLAMA_READY, (False, False)))

        threading.Thread(target=install, daemon=True).start()

    def _install_cuda(self):
        """Устанавливает torch с поддержкой CUDA."""
        self._log("Установка torch с CUDA...")
        self._set_busy(True, "Установка CUDA...")

        def install():
            try:
                import subprocess

                self._queue.put((QueueMsg.LOG, "  Удаление CPU-версии torch..."))
                subprocess.run("pip uninstall -y torch torchaudio", shell=True, timeout=120)
                self._queue.put((QueueMsg.LOG, "  Установка torch с CUDA (это займёт ~2 мин)..."))
                result = subprocess.run(
                    "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121",
                    shell=True,
                    timeout=600,
                )
                if result.returncode == 0:
                    self._queue.put((QueueMsg.LOG, "CUDA установлена! Перезапуск через 3 сек..."))
                    self._queue.put((QueueMsg.CUDA_INSTALLED, True))
                else:
                    self._queue.put((QueueMsg.LOG, "Ошибка установки. Установка CPU-версии..."))
                    subprocess.run(
                        "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu",
                        shell=True,
                        timeout=300,
                    )
                    self._queue.put((QueueMsg.LOG, "Установлена CPU-версия"))
            except Exception as exc:
                self._queue.put((QueueMsg.LOG, f"Ошибка: {exc}"))
                self._queue.put((QueueMsg.LOG, "Установка CPU-версии..."))
                subprocess.run(
                    "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu",
                    shell=True,
                    timeout=300,
                )
            finally:
                self._queue.put((QueueMsg.SET_BUSY, False))

        threading.Thread(target=install, daemon=True).start()

    # ------------------------------------------------------------------
    # Основная обработка
    # ------------------------------------------------------------------
    def _on_run(self):
        """Запускает транскрибацию в фоновом потоке."""
        if self._running:
            return

        files = self._listbox.get_files()
        if not files:
            messagebox.showwarning("Нет файлов", "Добавьте аудиофайлы для обработки.")
            return

        if not self._ffmpeg_ok:
            messagebox.showerror(
                "ffmpeg не найден", "Для работы required ffmpeg.\nУстановите: winget install ffmpeg"
            )
            return

        output_fmt = self._var_format.get()
        raw = self._text_prompt.get("1.0", "end-1c").strip()
        initial_prompt = None if raw in ("", "контекст (тема, имена, термины)") else raw
        qwen_available = self._enhancer._model_ok

        self._log(f"Начинаю обработку {len(files)} файлов (формат: {output_fmt.upper()})")
        if qwen_available:
            self._log("Улучшение через Qwen 2.5 3b включено")
        self._cancel = False
        self._btn_run.config(text="Остановить", command=self._on_stop)
        self._set_busy(True, "Загрузка модели Whisper large-v3...")
        self._btn_run.config(state=tk.NORMAL)

        def worker():
            self._running = True
            try:
                self._transcriber.load_model()
                total = len(files)
                from utils.gpu import get_torch_device

                device = get_torch_device().upper()
                self._queue.put((QueueMsg.LOG, f"  Модель: medium | Устройство: {device}"))

                start_time = time.time()
                completed_times = []

                for i, filepath in enumerate(files, 1):
                    if self._cancel:
                        self._queue.put((QueueMsg.LOG, "  Остановлено пользователем"))
                        break
                    msg = f"[{i}/{total}] {filepath.name}"
                    self._queue.put((QueueMsg.LOG, f"  {msg}"))
                    self._queue.put((QueueMsg.TRANSCRIBING, f"Транскрибация: {filepath.name}..."))

                    file_start = time.time()

                    whisper_mode = detect_quiet_audio(filepath, self._queue)
                    if whisper_mode:
                        self._queue.put((QueueMsg.LOG, "  Тихий звук — включаю усиленный режим"))

                    preproc_path = None
                    if whisper_mode:
                        self._queue.put((QueueMsg.LOG, "  Предобработка (компрессия)..."))
                        try:
                            preproc_path = preprocess_audio(filepath)
                            audio_to_transcribe = preproc_path
                        except Exception as exc:
                            self._queue.put(
                                (
                                    QueueMsg.LOG,
                                    f"  Ошибка предобработки: {exc}, работаю с оригиналом",
                                )
                            )
                            audio_to_transcribe = filepath
                    else:
                        audio_to_transcribe = filepath

                    kwargs = {
                        "language": "ru",
                        "task": "transcribe",
                        "verbose": True,
                    }
                    if initial_prompt:
                        kwargs["initial_prompt"] = initial_prompt

                    text = self._transcriber.transcribe(
                        audio_to_transcribe, queue=self._queue, **kwargs
                    )

                    if self._cancel:
                        self._queue.put((QueueMsg.LOG, "  Остановлено пользователем"))
                        break

                    file_end = time.time()
                    completed_times.append(file_end - file_start)

                    elapsed = file_end - start_time
                    avg_time = elapsed / i
                    remaining = (total - i) * avg_time
                    eta_min = int(remaining // 60)
                    eta_sec = int(remaining % 60)
                    eta_str = f"{eta_min}м {eta_sec}с" if eta_min > 0 else f"{eta_sec}с"

                    self._queue.put((QueueMsg.PROGRESS, (i, total, filepath.name, eta_str)))

                    if text:
                        preview = text[:100] + "..." if len(text) > 100 else text
                        self._queue.put(
                            (QueueMsg.LOG, f"  Распознано ({len(text)} символов): {preview}")
                        )

                    if text and qwen_available:
                        multi = self._var_multi_pass.get()
                        if multi:
                            self._queue.put(
                                (QueueMsg.LOG, "  Многопроходное улучшение Qwen (3 прохода)...")
                            )
                            try:

                                def mp_progress(msg):
                                    self._queue.put((QueueMsg.LOG, f"    {msg}"))

                                text = self._enhancer.enhance_multi_pass(
                                    text, initial_prompt or "", progress_callback=mp_progress
                                )
                                self._queue.put(
                                    (QueueMsg.LOG, "  Многопроходное улучшение завершено")
                                )
                            except Exception as exc:
                                self._queue.put(
                                    (QueueMsg.LOG, f"  Ошибка многопроходного улучшения: {exc}")
                                )
                        else:
                            self._queue.put((QueueMsg.LOG, "  Улучшение текста через Qwen..."))
                            try:
                                text = self._enhancer.enhance(text, initial_prompt or "")
                                self._queue.put((QueueMsg.LOG, "  Текст улучшен"))
                            except Exception as exc:
                                self._queue.put((QueueMsg.LOG, f"  Ошибка улучшения: {exc}"))

                    if preproc_path is not None and preproc_path.exists():
                        preproc_path.unlink(missing_ok=True)

                    if not text:
                        self._queue.put((QueueMsg.LOG, f"{filepath.name} — пустой результат"))
                        continue

                    out_path = filepath.with_suffix(f".{output_fmt}")

                    if output_fmt == "txt":
                        save_txt(out_path, text)
                    elif output_fmt == "docx":
                        save_docx(out_path, text)

                    self._queue.put((QueueMsg.LOG, f"{filepath.name} -> {out_path.name}"))

                if self._cancel:
                    self._queue.put((QueueMsg.CANCELLED, "Остановлено пользователем"))
                else:
                    self._queue.put((QueueMsg.DONE, f"Готово. Обработано {len(files)} файлов."))

            except Exception as exc:
                self._queue.put((QueueMsg.ERROR, f"Ошибка: {exc}"))
            finally:
                self._running = False

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    def _check_queue(self):
        """Периодически проверяет очередь сообщений от фонового потока."""
        try:
            while True:
                msg_type, msg = self._queue.get_nowait()

                if msg_type is QueueMsg.LOG:
                    self._log(msg)
                elif msg_type is QueueMsg.SET_BUSY:
                    self._set_busy(msg)
                elif msg_type is QueueMsg.CUDA_INSTALLED:
                    self._log("CUDA установлена! Перезапуск через 3 сек...")
                    self.root.after(3000, self._restart_app)
                elif msg_type is QueueMsg.PROGRESS:
                    current, total, filename, eta = msg
                    self._set_progress_text(f"[{current}/{total}] {filename} — осталось ~{eta}")
                elif msg_type is QueueMsg.TRANSCRIBING:
                    self._set_progress_text(msg)
                elif msg_type is QueueMsg.WHISPER_PROGRESS:
                    p = msg
                    self._set_progress_text(
                        f"{p['percent']}% | {p['current']}/{p['total']} "
                        f"[{p['elapsed']}<{p['remaining']}, {p['speed']} frames/s]"
                    )
                elif msg_type is QueueMsg.OLLAMA_READY:
                    ok, model = msg
                    if ok and model:
                        self._enhancer._ollama_ok = True
                        self._enhancer._model_ok = True
                        self._set_busy(False)
                        self._log("Qwen 2.5 3b: доступна — текст будет улучшаться автоматически")
                    elif ok and not model:
                        self._enhancer._ollama_ok = True
                        self._log("Qwen 2.5 3b: модель не скачана")
                    else:
                        self._log("Ollama не найдена. Улучшение текста будет пропущено.")
                        if messagebox.askyesno(
                            "Ollama", "Для улучшения текста требуется Ollama. Установить сейчас?"
                        ):
                            self._install_ollama()
                elif msg_type in (QueueMsg.DONE, QueueMsg.ERROR, QueueMsg.CANCELLED):
                    self._set_busy(False)
                    self._btn_run.config(text="Обработать", command=self._on_run, state=tk.NORMAL)
                    self._log(msg)
                    if msg_type is QueueMsg.CANCELLED:
                        messagebox.showinfo("Остановлено", msg)
                    elif msg_type is QueueMsg.ERROR:
                        messagebox.showerror("Ошибка", msg)
                    else:
                        messagebox.showinfo("Готово", msg)
        except queue.Empty:
            pass
        finally:
            self.root.after(QUEUE_POLL_MS, self._check_queue)

    # ------------------------------------------------------------------
    def _restart_app(self):
        """Перезапускает приложение."""
        import sys

        python = sys.executable
        os.execl(python, python, *sys.argv)

    # ------------------------------------------------------------------
    def run(self):
        """Запускает главный цикл."""
        self.root.mainloop()
