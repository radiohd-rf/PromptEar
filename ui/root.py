"""PromptEarApp — оркестратор GUI и бизнес-логики."""

import contextlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
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
from core.events import (
    CancelledEvent,
    CudaInstalledEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    OllamaReadyEvent,
    ProgressEvent,
    QueueMsg,
    SetBusyEvent,
    TranscribingEvent,
    WhisperProgressEvent,
)
from core.installers import CudaInstaller, OllamaInstaller
from core.models import AudioFile, PipelineConfig
from core.pipeline import run_pipeline
from processing.enhancer import OllamaEnhancer
from processing.transcriber import Transcriber
from ui.handlers import EventDispatcher
from ui.theme import ThemeManager
from ui.titlebar import TitleBar
from ui.widgets import PlaceholderEntry, PlaceholderListbox
from utils.files import find_audio_files

HAS_DND = __import__("importlib").util.find_spec("tkinterdnd2") is not None


class PromptEarApp:
    """Главный класс приложения: оркестрирует UI и pipeline."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PromptEar — транскрибация аудио")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.resizable(False, True)

        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._running = False

        self._transcriber = Transcriber()
        self._transcriber.load_model_async()
        self._enhancer = OllamaEnhancer()
        self._ffmpeg_ok = shutil.which("ffmpeg") is not None

        self._spinner_frames = SPINNER_FRAMES
        self._spinner_index = 0
        self._spinner_running = False
        self._spinner_job: str | None = None

        self._log_expanded = True

        self.theme = ThemeManager(root)
        self.dispatcher = EventDispatcher()
        self._build_ui()
        self.titlebar = TitleBar(root, self, self.theme)
        self.titlebar.build(self._main)

        from utils.gpu import detect_and_report

        gpu_info = detect_and_report()
        self._log(f"GPU: nvidia={gpu_info['has_nvidia_gpu']}, "
                  f"torch_cuda={gpu_info['torch_cuda_installed']}, device={gpu_info['device']}")
        if gpu_info["need_install"]:
            self._log("NVIDIA GPU found, torch without CUDA - auto-installing...")
            CudaInstaller.install(self._queue.put)
        elif gpu_info["has_nvidia_gpu"] and gpu_info["torch_cuda_installed"]:
            self._log("GPU: NVIDIA + CUDA готовы")
        else:
            self._log("GPU: не обнаружена (CPU)")

        if HAS_DND:
            self.root.drop_target_register("DND_Files")
            self.root.dnd_bind("<<Drop>>", self._on_drop)

        self._process_cli_args()
        self._check_ollama_async()
        self._check_queue()

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        self._text_log.config(state=tk.NORMAL)
        self._text_log.insert(tk.END, message + "\n")
        self._text_log.see(tk.END)
        self._text_log.config(state=tk.DISABLED)
        from utils.logger import get_logger
        get_logger().info(message)

    # ── Queue check (thread-safe, runs in GUI thread) ───────────────────

    def _dispatch_event(self, event) -> None:
        """Обрабатывает одно событие из очереди."""
        if isinstance(event, LogEvent):
            self._log(event.message)
        elif isinstance(event, ProgressEvent):
            self._set_progress_text(f"[{event.current}/{event.total}] {event.filename} — осталось ~{event.eta}")
        elif isinstance(event, TranscribingEvent):
            self._set_progress_text(event.message)
        elif isinstance(event, WhisperProgressEvent):
            self._set_progress_text(
                f"{event.percent}% | {event.current}/{event.total} "
                f"[{event.elapsed}<{event.remaining}, {event.speed} frames/s]"
            )
        elif isinstance(event, SetBusyEvent):
            self._set_busy(event.busy)
        elif isinstance(event, OllamaReadyEvent):
            if event.ollama_ok and event.model_ok:
                self._enhancer._ollama_ok = True
                self._enhancer._model_ok = True
                self._set_busy(False)
                self._log("Qwen 2.5 3b: доступна — текст будет улучшаться автоматически")
            elif event.ollama_ok and not event.model_ok:
                self._enhancer._ollama_ok = True
                self._log("Qwen 2.5 3b: модель не скачана")
            else:
                self._log("Ollama не найдена. Улучшение текста будет пропущено.")
                if messagebox.askyesno("Ollama", "Для улучшения требуется Ollama. Установить сейчас?"):
                    OllamaInstaller.install(self._enhancer, self._queue.put)
        elif isinstance(event, CudaInstalledEvent):
            self._log("CUDA установлена! Перезапуск через 3 сек...")
            self.root.after(3000, self._restart_app)
        elif isinstance(event, (DoneEvent, ErrorEvent, CancelledEvent)):
            self._set_busy(False)
            self._btn_run.config(text="Обработать", command=self._on_run, state=tk.NORMAL)
            self._log(event.message)
            if isinstance(event, CancelledEvent):
                messagebox.showinfo("Остановлено", event.message)
            elif isinstance(event, ErrorEvent):
                messagebox.showerror("Ошибка", event.message)
            elif isinstance(event, DoneEvent):
                messagebox.showinfo("Готово", event.message)

    def _check_queue(self) -> None:
        try:
            while True:
                event = self._queue.get_nowait()
                self._dispatch_event(event)
        except queue.Empty:
            pass
        finally:
            self.root.after(QUEUE_POLL_MS, self._check_queue)

    # ── Theme ────────────────────────────────────────────────────────────

    def _toggle_theme(self) -> None:
        self.theme.toggle()
        self.titlebar.update_theme()
        self.theme.apply_to_all(self.root)

        self._entry_path._normal_fg = self.theme.fg  # type: ignore[attr-defined]
        self._entry_path._placeholder_fg = self.theme.ph  # type: ignore[attr-defined]
        self._listbox._normal_fg = self.theme.fg  # type: ignore[attr-defined]
        self._listbox._placeholder_fg = self.theme.ph  # type: ignore[attr-defined]
        placeholder_active = self._entry_path.get() == self._entry_path._placeholder  # type: ignore[attr-defined]
        self._entry_path.configure(bg=self.theme.bg, fg=self.theme.ph if placeholder_active else self.theme.fg)
        self._listbox.configure(bg=self.theme.bg, fg=self.theme.ph if self._listbox.has_placeholder() else self.theme.fg)
        self._text_prompt.configure(bg=self.theme.bg, fg=self.theme.fg, insertbackground=self.theme.fg)
        self._text_log.configure(bg=self.theme.bg, fg=self.theme.fg, insertbackground=self.theme.fg)
        self._btn_toggle_log.configure(bg=self.theme.bg, fg=self.theme.status)
        self._root_bg = self.theme.bg

    # ── UI Building ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root.configure(bg=COLORS["bg"])
        self.root.option_add("*Font", (FONT_FAMILY, FONT_SIZE))
        self.root.option_add("*HighlightThickness", 0)

        self._main = tk.Frame(self.root, bg=COLORS["bg"])
        self._main.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        main = self._main

        # Path + Browse
        row_path = tk.Frame(main, bg=COLORS["bg"])
        row_path.pack(fill=tk.X, pady=(0, 6))

        self._entry_path = PlaceholderEntry(
            row_path, placeholder='выберите папку с аудио: нажмите "Обзор"',
            bg=COLORS["bg"], fg=COLORS["ph"], normal_fg=COLORS["fg"],
            insertbackground=COLORS["fg"], relief=tk.SOLID, bd=1,
            font=(FONT_FAMILY, FONT_SIZE),
        )
        self._entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_browse = tk.Button(
            row_path, text="Обзор", command=self._on_browse,
            bg=COLORS["bg"], fg=COLORS["fg"], activebackground=COLORS["btn_active_bg"],
            activeforeground=COLORS["fg"], font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.SOLID, bd=1, highlightthickness=0, padx=12, pady=0, cursor="hand2",
        )
        btn_browse.pack(side=tk.RIGHT)

        # File list
        frm_list = tk.Frame(main, bg=COLORS["bg"], highlightthickness=1, highlightbackground=COLORS["border"])
        frm_list.pack(fill=tk.X, pady=(0, 6))

        self._listbox = PlaceholderListbox(
            frm_list, placeholder="перетащите файлы сюда", height=4,
            selectmode=tk.EXTENDED, font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.FLAT, highlightthickness=0, bg=COLORS["bg"], fg=COLORS["ph"],
            normal_fg=COLORS["fg"], selectbackground="#eee", selectforeground=COLORS["fg"],
            borderwidth=0,
        )
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_files = ttk.Scrollbar(frm_list, orient=tk.VERTICAL, command=self._listbox.yview)
        scroll_files.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.config(yscrollcommand=scroll_files.set)

        self._listbox.bind("<Delete>", self._remove_selected)
        if HAS_DND:
            self._listbox.drop_target_register("DND_Files")
            self._listbox.dnd_bind("<<Drop>>", self._on_drop)
        self._listbox.bind("<Button-3>", self._show_list_menu)

        # Context
        frm_ctx = tk.Frame(main, bg=COLORS["bg"], highlightthickness=1, highlightbackground=COLORS["border"])
        frm_ctx.pack(fill=tk.X, pady=(0, 4))

        self._text_prompt = tk.Text(
            frm_ctx, height=2, wrap=tk.WORD, font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.FLAT, highlightthickness=0, bg=COLORS["bg"], fg=COLORS["ph"],
            borderwidth=0, insertbackground=COLORS["fg"],
        )
        self._text_prompt.insert("1.0", "контекст (тема, имена, термины)")
        self._text_prompt.pack(fill=tk.X)
        self._text_prompt.bind("<FocusIn>", self._on_prompt_focus)
        self._text_prompt.bind("<FocusOut>", self._on_prompt_blur)
        self._text_prompt.bind("<Button-3>", self._show_text_menu)

        # Format + multi-pass + button
        row_fmt = tk.Frame(main, bg=COLORS["bg"])
        row_fmt.pack(fill=tk.X, pady=(0, 4))

        self._var_format = tk.StringVar(value="docx")
        for fmt in OUTPUT_FORMATS:
            tk.Radiobutton(
                row_fmt, text=fmt.upper(), variable=self._var_format, value=fmt,
                bg=COLORS["bg"], fg=COLORS["fg"], selectcolor=COLORS["bg"],
                activebackground=COLORS["bg"], activeforeground=COLORS["fg"],
                font=(FONT_FAMILY, FONT_SIZE),
            ).pack(side=tk.LEFT, padx=(0, 10))

        self._var_multi_pass = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row_fmt, text="Многопроходное улучшение текста",
            variable=self._var_multi_pass,
            bg=COLORS["bg"], fg=COLORS["fg"], selectcolor=COLORS["bg"],
            activebackground=COLORS["bg"], activeforeground=COLORS["fg"],
            font=(FONT_FAMILY, FONT_SIZE),
        ).pack(side=tk.LEFT, padx=(20, 0))

        self._btn_run = tk.Button(
            row_fmt, text="Обработать", command=self._on_run,
            bg=COLORS["bg"], fg=COLORS["fg"], activebackground=COLORS["btn_active_bg"],
            activeforeground=COLORS["fg"], font=(FONT_FAMILY, FONT_SIZE),
            relief=tk.SOLID, bd=1, highlightthickness=0, padx=12, pady=4, cursor="hand2",
        )
        self._btn_run.pack(side=tk.RIGHT)

        # Progress
        row_progress = tk.Frame(main, bg=COLORS["bg"])
        row_progress.pack(fill=tk.X, pady=(2, 4))

        self._spinner_label = tk.Label(
            row_progress, text="", anchor=tk.W, bg=COLORS["bg"], fg=COLORS["spinner"],
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
        )
        self._spinner_label.pack(side=tk.LEFT)

        self._status_label = tk.Label(
            row_progress, text="", anchor=tk.E, bg=COLORS["bg"], fg=COLORS["status"],
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
        )
        self._status_label.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        # Log section
        self._frm_log_section = tk.Frame(main, bg=COLORS["bg"])
        self._frm_log_section.pack(fill=tk.BOTH, expand=True)

        frm_log_toggle = tk.Frame(self._frm_log_section, bg=COLORS["bg"],
                                  highlightthickness=1, highlightbackground=COLORS["border"])
        self._btn_toggle_log = tk.Label(
            frm_log_toggle, text="▼ Логи", font=(FONT_FAMILY, FONT_SIZE_SMALL),
            fg=COLORS["status"], bg=COLORS["bg"], cursor="hand2",
        )
        self._btn_toggle_log.pack(side=tk.LEFT, padx=4)
        self._btn_toggle_log.bind("<Button-1>", lambda e: self._toggle_log())
        frm_log_toggle.pack(fill=tk.X)

        self._frm_log = tk.Frame(self._frm_log_section, bg=COLORS["bg"],
                                 highlightthickness=1, highlightbackground=COLORS["border"])
        self._frm_log.pack(fill=tk.BOTH, expand=True)

        self._text_log = tk.Text(
            self._frm_log, height=5, wrap=tk.WORD, state=tk.DISABLED,
            font=(FONT_FAMILY, FONT_SIZE_SMALL), relief=tk.FLAT, highlightthickness=0,
            bg=COLORS["bg"], fg=COLORS["fg"], borderwidth=0, insertbackground=COLORS["fg"],
        )
        self._text_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_log = ttk.Scrollbar(self._frm_log, orient=tk.VERTICAL, command=self._text_log.yview)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self._text_log.config(yscrollcommand=scroll_log.set)
        self._text_log.bind("<Button-3>", self._show_log_menu)

    # ── Log toggle ───────────────────────────────────────────────────────

    def _toggle_log(self) -> None:
        if self._log_expanded:
            self._frm_log.pack_forget()
            self._btn_toggle_log.config(text="▶ Логи")
            self.root.geometry(f"{WINDOW_WIDTH}x360")
        else:
            self._frm_log.pack(fill=tk.BOTH, expand=True)
            self._btn_toggle_log.config(text="▼ Логи")
            self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self._log_expanded = not self._log_expanded

    # ── Spinner ──────────────────────────────────────────────────────────

    def _start_spinner(self, text: str = "") -> None:
        self._spinner_running = True
        self._status_label.config(text=text)
        self._animate_spinner()

    def _stop_spinner(self) -> None:
        self._spinner_running = False
        if self._spinner_job:
            self.root.after_cancel(self._spinner_job)
            self._spinner_job = None
        self._spinner_label.config(text="")
        self._status_label.config(text="")

    def _animate_spinner(self) -> None:
        if not self._spinner_running:
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        self._spinner_label.config(text=self._spinner_frames[self._spinner_index])
        self._spinner_job = self.root.after(120, self._animate_spinner)

    def _set_progress_text(self, text: str) -> None:
        self._status_label.config(text=text)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self._btn_run.config(state=state)
        if busy:
            self._start_spinner(label)
        else:
            self._stop_spinner()

    # ── Event handlers ───────────────────────────────────────────────────

    def _on_browse(self) -> None:
        folder = filedialog.askdirectory(title="Выберите папку с аудиофайлами")
        if not folder:
            return
        self._entry_path.set_text(folder)
        self._add_files([folder])

    def _on_stop(self) -> None:
        self._cancel.set()
        self._btn_run.config(text="Остановка...", state=tk.DISABLED)
        self._log("Остановка после текущего файла...")

    def _on_drop(self, event) -> None:
        raw = event.data
        if not raw:
            return
        files = []
        for part in raw.split():
            part = part.strip("{}")
            if os.path.exists(part):
                files.append(part)
        self._add_files(files)

    def _remove_selected(self, event=None) -> None:
        selected = self._listbox.curselection()
        for i in reversed(selected):
            self._listbox.delete(i)
        self._listbox._show_placeholder()

    def _show_list_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, FONT_SIZE))
        menu.add_command(label="Удалить", command=self._remove_selected)
        menu.add_command(label="Очистить всё", command=self._listbox.clear)
        menu.tk_popup(event.x_root, event.y_root)

    def _show_log_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, FONT_SIZE))
        menu.add_command(label="Копировать", command=self._copy_log_text)
        menu.tk_popup(event.x_root, event.y_root)

    def _copy_log_text(self) -> None:
        try:
            text = self._text_log.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _show_text_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, FONT_SIZE))
        menu.add_command(label="Копировать", command=self._copy_text)
        menu.add_command(label="Вырезать", command=self._cut_text)
        menu.add_command(label="Вставить", command=self._paste_text)
        menu.tk_popup(event.x_root, event.y_root)

    def _copy_text(self) -> None:
        try:
            text = self._text_prompt.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _cut_text(self) -> None:
        self._copy_text()
        with contextlib.suppress(tk.TclError):
            self._text_prompt.delete("sel.first", "sel.last")

    def _paste_text(self) -> None:
        try:
            text = self.root.clipboard_get()
            self._text_prompt.insert("insert", text)
        except tk.TclError:
            pass

    def _on_prompt_focus(self, event) -> None:
        txt = self._text_prompt.get("1.0", "end-1c")
        if txt == "контекст (тема, имена, термины)":
            self._text_prompt.delete("1.0", tk.END)
            self._text_prompt.config(fg=self.theme.fg)

    def _on_prompt_blur(self, event) -> None:
        if not self._text_prompt.get("1.0", "end-1c").strip():
            self._text_prompt.delete("1.0", tk.END)
            self._text_prompt.insert("1.0", "контекст (тема, имена, термины)")
            self._text_prompt.config(fg=self.theme.ph)

    # ── Files ────────────────────────────────────────────────────────────

    def _add_files(self, paths) -> None:
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
                self._entry_path.set_text(os.path.normpath(p))
                break

    def _process_cli_args(self) -> None:
        args = sys.argv[1:]
        if not args:
            return
        files = [a for a in args if not a.startswith("-")]
        if files:
            self._add_files(files)

    # ── Ollama ───────────────────────────────────────────────────────────

    def _check_ollama_async(self) -> None:
        def install_cb():
            OllamaInstaller.install(self._enhancer, self._queue.put)
        OllamaInstaller.check_ollama(self._enhancer, self._queue.put, install_cb)

    def _install_ollama(self) -> None:
        OllamaInstaller.install(self._enhancer, self._queue.put)

    # ── CUDA install ────────────────────────────────────────────────────

    def _install_cuda(self) -> None:
        CudaInstaller.install(self._queue.put)

    # ── Pipeline ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        if self._running:
            return

        files = self._listbox.get_files()
        if not files:
            messagebox.showwarning("Нет файлов", "Добавьте аудиофайлы для обработки.")
            return

        if not self._ffmpeg_ok:
            messagebox.showerror("ffmpeg не найден",
                                 "Для работы required ffmpeg.\nУстановите: winget install ffmpeg")
            return

        output_fmt = self._var_format.get()
        raw = self._text_prompt.get("1.0", "end-1c").strip()
        qwen_available = self._enhancer._model_ok

        self._log(f"Начинаю обработку {len(files)} файлов (формат: {output_fmt.upper()})")
        if qwen_available:
            self._log("Улучшение через Qwen 2.5 3b включено")
        self._cancel.clear()
        self._btn_run.config(text="Остановить", command=self._on_stop)
        self._set_busy(True, "Загрузка модели Whisper large-v3...")
        self._btn_run.config(state=tk.NORMAL)

        pipeline_config = PipelineConfig(
            output_format=output_fmt,
            multi_pass=self._var_multi_pass.get(),
            initial_prompt=None if raw in ("", "контекст (тема, имена, термины)") else raw,
            qwen_available=qwen_available,
        )
        audio_files = [AudioFile(path=f) for f in files]

        def worker():
            self._running = True
            try:
                run_pipeline(
                    files=audio_files,
                    config=pipeline_config,
                    emit=self._queue.put,
                    cancel=self._cancel,
                    transcriber=self._transcriber,
                    enhancer=self._enhancer,
                )
            finally:
                self._running = False

        threading.Thread(target=worker, daemon=True).start()

    # ── Restart ──────────────────────────────────────────────────────────

    def _restart_app(self) -> None:
        python = sys.executable
        os.execl(python, python, *sys.argv)

    def run(self) -> None:
        self.root.mainloop()
