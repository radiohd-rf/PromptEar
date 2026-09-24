"""Фоновые скачивания: модели Whisper, Gemma (GGUF), llama.cpp.

DownloadManager держит ОДИН активный download (см. спека unified-build §6).
Прогресс репортится событиями DownloadProgressEvent / DownloadDoneEvent /
DownloadFailedEvent через emit-колбэк (сервер рассылает их в SSE-канал).
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import threading
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from config import (
    GGUF_URL,
    LLAMA_CPU_URL,
    LLAMA_DIR,
    LLM_MODEL_DIR,
)
from core import gigaam_models as gm
from core import settings as settings_store
from core import whisper_models as wm
from core.events import (
    DownloadDoneEvent,
    DownloadFailedEvent,
    DownloadProgressEvent,
    PipelineEvent,
)

CHUNK = 1 << 16  # 64 KiB

# Файлы faster-whisper модели в репозитории, необходимые для работы.
_HF_FILE_RE = re.compile(r"^(model\.bin|.*\.json|tokenizer.*|vocabulary.*)$", re.I)
# Файлы GigaAM v3 в репозитории, необходимые для работы (remote code).
_GIGAAM_FILE_RE = re.compile(
    r"^(config\.json|modeling_gigaam\.py|tokenizer\.model|pytorch_model\.bin)$"
)


def _hf_api(repo_id: str) -> str:
    return f"https://huggingface.co/api/models/{repo_id}"


def _hf_file_url(repo_id: str, name: str, revision: str | None = None) -> str:
    ref = revision or "main"
    return f"https://huggingface.co/{repo_id}/resolve/{ref}/{name}"


def fetch_file(
    url: str,
    dest: Path,
    on_progress: Callable[[int, int | None], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Скачивает файл по URL во временный `.part` и переименовывает в dest.

    on_progress(done_bytes, total_bytes) вызывается по мере получения данных;
    total_bytes=None, если сервер не прислал Content-Length.
    cancel — событие отмены: при установке скачивание прерывается,
    временный `.part` удаляется, поднимается RuntimeError("Скачивание отменено").
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=(15, 60)) as r:
            r.raise_for_status()
            header = r.headers.get("Content-Length")
            total = int(header) if header else None
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("Скачивание отменено")
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
        if on_progress is not None:
            on_progress(done, total)
        tmp.replace(dest)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink(missing_ok=True)
        raise


def list_hf_files(repo_id: str) -> list[str]:
    """Возвращает имена файлов репозитория HF, нужных faster-whisper."""
    r = requests.get(_hf_api(repo_id), timeout=(15, 60))
    r.raise_for_status()
    data = r.json()
    files = [
        sibling.get("rfilename")
        for sibling in data.get("siblings", [])
        if sibling.get("rfilename")
    ]
    return [name for name in files if _HF_FILE_RE.match(name)]


def download_gigaam_variant(
    variant: str,
    dest: Path,
    on_progress: Callable[[str, int, int | None], None] | None = None,
) -> Path:
    """Скачивает вариант GigaAM v3 (ветка варианта) в ПЛОСКУЮ папку dest.

    on_progress(label, done_bytes, total_bytes). Список файлов берётся из HF API
    (имя ветки = вариант); общий размер — приблизительно из каталога.
    """
    repo = gm.GIGAAM_REPO
    approx_total = gm.size_mb(variant) * 1024 * 1024
    r = requests.get(_hf_api(repo), timeout=(15, 60))
    r.raise_for_status()
    files = [
        s.get("rfilename")
        for s in r.json().get("siblings", [])
        if s.get("rfilename") and _GIGAAM_FILE_RE.match(s["rfilename"])
    ]
    if "pytorch_model.bin" not in files:
        raise RuntimeError(f"В репозитории {repo} нет pytorch_model.bin")
    dest.mkdir(parents=True, exist_ok=True)

    class _Fin:
        done = 0
        _last = 0

        def emit(self, label: str, file_done: int, file_total: int | None) -> None:
            delta = file_done - self._last
            self._last = file_done
            if delta > 0:
                self.done += delta
            if on_progress is not None:
                on_progress(label, self.done, max(approx_total, 1))

    fin = _Fin()

    def per_file(label: str) -> Callable[[int, int | None], None]:
        fin._last = 0

        def cb(done: int, total: int | None) -> None:
            fin.emit(label, done, total)

        return cb

    for idx, name in enumerate(files, 1):
        label = f"{variant}: файл {idx}/{len(files)} ({name})"
        fetch_file(
            _hf_file_url(repo, name, revision=variant),
            dest / name,
            on_progress=per_file(label),
        )
    if on_progress is not None:
        on_progress(f"{variant}: готово", fin.done, max(approx_total, 1))
    if not (dest / "pytorch_model.bin").exists():
        raise RuntimeError(f"GigaAM {variant}: pytorch_model.bin не найден")
    return dest


def download_whisper_model(
    alias: str,
    dest: Path,
    on_progress: Callable[[str, int, int | None], None] | None = None,
) -> Path:
    """Скачивает faster-whisper модель в ПЛОСКУЮ папку dest (без cache-раскладки).

    on_progress(label, done_bytes, total_bytes) — label вида «tiny: файл 3/9».
    Спиcок файлов приходит из HF API первым запросом; общий ожидаемый размер
    берётся приблизительно из каталога (для процентов), total_bytes — он же.
    """
    repo = wm.resolve(alias)
    approx_total = wm.size_mb(alias) * 1024 * 1024
    files = list_hf_files(repo)
    if not files:
        raise RuntimeError(f"Не удалось найти файлы модели {repo}")
    if not any(name == "model.bin" for name in files):
        raise RuntimeError(f"В репозитории {repo} нет model.bin")
    dest.mkdir(parents=True, exist_ok=True)

    class _Fin:
        """Суммарный прогресс по всем файлам модели.

        fetch_file зовёт колбэк с cumulative done_bytes текущего файла
        (и дублирует финальный вызов), поэтому складываем только DЕЛЬТУ
        между вызовами, а не сам file_done — иначе прогресс раздувается.
        """

        done = 0
        _last = 0

        def emit(self, label: str, file_done: int, file_total: int | None) -> None:
            delta = file_done - self._last
            self._last = file_done
            if delta > 0:
                self.done += delta
            if on_progress is not None:
                on_progress(label, self.done, max(approx_total, 1))

    fin = _Fin()

    def per_file(label: str) -> Callable[[int, int | None], None]:
        fin._last = 0  # новый файл: его cumulative отсчитывается с нуля

        def cb(done: int, total: int | None) -> None:
            fin.emit(label, done, total)

        return cb

    for idx, name in enumerate(files, 1):
        label = f"{alias}: файл {idx}/{len(files)} ({name})"
        fetch_file(
            _hf_file_url(repo, name),
            dest / name,
            on_progress=per_file(label),
        )
    if on_progress is not None:
        on_progress(f"{alias}: готово", fin.done, max(approx_total, 1))
    if not (dest / "model.bin").exists():
        raise RuntimeError(f"Модель {alias}: model.bin не найден после скачивания")
    return dest


class DownloadManager:
    """Один активный download за раз. start() кидает RuntimeError, если занято."""

    def __init__(self, emit: Callable[[PipelineEvent], None]):
        self._emit = emit
        self._lock = threading.Lock()
        self._active: dict | None = None  # {"id":.., "kind":.., "label":..}

    @property
    def active(self) -> dict | None:
        with self._lock:
            return dict(self._active) if self._active else None

    def status(self) -> dict:
        active = self.active
        return {"active": active}

    def start(self, kind: str, model: str | None = None) -> str:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("Уже идёт другое скачивание")
            download_id = uuid.uuid4().hex[:12]
            labels = {
                "whisper": f"Модель транскрибации {model or ''}".strip(),
                "gigaam_deps": "GigaAM: torch (CUDA) + pyannote (~2.5 ГБ)",
                "gigaam_model": f"GigaAM v3 {model or ''}".strip(),
                "gemma": "Модель ИИ (Gemma, ~3 ГБ)",
                "llama": "llama.cpp",
                "llama_gpu": "llama.cpp: CUDA-сборка (~500 МБ)",
            }
            self._active = {
                "id": download_id,
                "kind": kind,
                "model": model,
                "label": labels.get(kind, kind),
            }
        thread = threading.Thread(
            target=self._run,
            args=(kind, model, download_id),
            daemon=True,
        )
        thread.start()
        return download_id

    # ── Внутреннее ────────────────────────────────────────────────────────

    def _run(self, kind: str, model: str | None, download_id: str) -> None:
        try:
            self._progress(download_id, "Старт", 0, 0.0, None)
            if kind == "whisper":
                model = model or settings_store.load().get("asr_backend", "whisper_base")
                self._run_whisper(download_id, model)
            elif kind == "gigaam_deps":
                self._run_gigaam_deps(download_id)
            elif kind == "gigaam_model":
                model = model or "e2e_rnnt"
                self._run_gigaam_model(download_id, model)
            elif kind == "gemma":
                self._run_gemma(download_id)
            elif kind == "llama":
                self._run_llama(download_id)
            elif kind == "llama_gpu":
                self._run_llama_gpu(download_id)
            else:
                raise RuntimeError(f"Неизвестный тип скачивания: {kind}")
            self._emit(DownloadDoneEvent(download_id))
        except Exception as exc:
            self._emit(DownloadFailedEvent(download_id, str(exc)))
        finally:
            with self._lock:
                self._active = None

    def _run_whisper(self, download_id: str, model: str) -> None:
        alias = model.removeprefix("whisper_") if model.startswith("whisper_") else model
        if alias not in wm.WHISPER_CATALOG:
            raise RuntimeError(f"Неизвестная модель Whisper: {model}")
        if wm.is_installed(alias):
            # уже установлена — просто переключаем на неё и чистим остальные
            wm.remove_others(alias)
            gm.remove_all()  # на диске остаётся только выбранный движок
            settings_store.save({"whisper_model": alias, "asr_backend": f"whisper_{alias}"})
            size = wm.size_mb(alias)
            self._progress(download_id, f"Модель {alias} уже установлена", 100, 0, size)
            return

        def on_progress(label: str, done: int, total: int | None) -> None:
            pct = min(99, int(done * 100 / max(total or 1, 1)))
            done_mb = done / (1024 * 1024)
            total_mb = (total or 0) / (1024 * 1024)
            self._progress(download_id, label, pct, done_mb, total_mb)

        download_whisper_model(alias, wm.model_dir(alias), on_progress=on_progress)
        self._progress(download_id, "Удаление предыдущих моделей…", 99, 0, None)
        wm.remove_others(alias)
        gm.remove_all()  # на диске остаётся только выбранный движок
        settings_store.save({"whisper_model": alias, "asr_backend": f"whisper_{alias}"})
        size = wm.size_mb(alias)
        self._progress(download_id, f"Модель {alias} готова", 100, size, size)

    @staticmethod
    def _transformers_5() -> bool:
        """transformers >= 5 ломает загрузку GigaAM (meta-инициализация)."""
        try:
            import transformers

            return int(transformers.__version__.split(".")[0]) >= 5
        except Exception:
            return False

    def _run_gigaam_deps(self, download_id: str) -> None:
        """Ставит torch (CUDA) + pyannote + transformers<5 в venv приложения."""
        try:
            from processing import gigaam as gigaam_engine

            if gigaam_engine.gigaam_deps_ok() and not self._transformers_5():
                self._progress(download_id, "GigaAM уже установлен", 100, 0, 0)
                return
        except Exception:
            pass
        import sys

        def pip(args: list[str], label: str) -> None:
            self._progress(download_id, label, 0, 0, None)
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--timeout", "120", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                tail = (result.stderr or result.stdout or "").strip()[-300:]
                raise RuntimeError(f"Ошибка установки {label}: {tail}")

        pip(
            [
                "torch==2.14.0",
                "torchaudio==2.11.0",
                "--index-url",
                "https://download.pytorch.org/whl/cu126",
            ],
            "Установка torch (CUDA, ~2.5 ГБ)…",
        )
        self._progress(download_id, "torch установлен", 55, 0, None)
        pip(
            [
                "pyannote.audio",
                "hydra-core",
                "omegaconf",
                "sentencepiece",
                "transformers==4.57.1",
            ],
            "Установка pyannote (VAD), transformers и зависимостей…",
        )
        self._progress(download_id, "GigaAM готов", 100, 0, 0)

    def _run_gigaam_model(self, download_id: str, model: str) -> None:
        """Скачивает выбранный вариант GigaAM v3 и удаляет остальные."""
        alias = model.removeprefix("gigaam_")
        if alias not in gm.GIGAAM_CATALOG:
            raise RuntimeError(f"Неизвестный вариант GigaAM: {model}")
        if gm.is_installed(alias):
            gm.remove_others(alias)
            wm.remove_all()  # на диске остаётся только выбранный движок
            settings_store.save({"asr_backend": model})
            self._progress(download_id, f"GigaAM {alias} уже установлен", 100, 0, 428)
            return

        def on_progress(label: str, done: int, total: int | None) -> None:
            pct = min(99, int(done * 100 / max(total or 1, 1)))
            self._progress(
                download_id,
                label,
                pct,
                done / (1024 * 1024),
                (total or 0) / (1024 * 1024),
            )

        download_gigaam_variant(alias, gm.model_dir(alias), on_progress=on_progress)
        self._progress(download_id, "Удаление предыдущих моделей…", 99, 0, None)
        gm.remove_others(alias)
        wm.remove_all()  # на диске остаётся только выбранный движок
        settings_store.save({"asr_backend": model})
        self._progress(download_id, f"GigaAM {alias} готов", 100, 428, 428)

    def _run_gemma(self, download_id: str) -> None:
        if not GGUF_URL:
            raise RuntimeError("URL модели ИИ (GGUF_URL) не настроен — модель недоступна")
        if LLM_MODEL_DIR.joinpath("gemma-4-E2B-it-UD-Q4_K_XL.gguf").exists():
            self._progress(download_id, "Модель ИИ уже установлена", 100, 0, 0)
            return
        from core.installers import GemmaInstaller

        def on_progress(done: int, total: int | None) -> None:
            pct = min(99, int(done * 100 / max(total or 1, 1)))
            self._progress(
                download_id,
                "Скачивание модели ИИ (Gemma, ~3 ГБ)…",
                pct,
                done / (1024 * 1024),
                total / (1024 * 1024) if total else None,
            )

        GemmaInstaller.download_and_start(on_progress=on_progress)
        self._progress(download_id, "Модель ИИ готова", 100, 0, 0)

    def _run_llama(self, download_id: str) -> None:
        server_exe = LLAMA_DIR / "llama-server.exe"
        if server_exe.exists():
            self._progress(download_id, "llama.cpp уже установлен", 100, 0, 0)
            return
        zip_path = LLAMA_DIR / "llama.zip"
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        self._progress(download_id, "Скачивание llama.cpp…", 0, 0, None)

        def on_progress(done: int, total: int | None) -> None:
            pct = min(99, int(done * 100 / max(total or 1, 1)))
            done_mb = done / (1024 * 1024)
            total_mb = (total or 0) / (1024 * 1024)
            self._progress(download_id, "Скачивание llama.cpp…", pct, done_mb, total_mb)

        fetch_file(LLAMA_CPU_URL, zip_path, on_progress=on_progress)
        self._progress(download_id, "Распаковка llama.cpp…", 99, 0, None)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(LLAMA_DIR)
        zip_path.unlink()
        self._progress(download_id, "llama.cpp готов", 100, 0, 0)

    def _run_llama_gpu(self, download_id: str) -> None:
        """Ставит CUDA-сборку llama.cpp и перезапускает движок на видеокарте."""
        from processing.enhancer import LlamaCppEnhancer

        enhancer = LlamaCppEnhancer()
        if enhancer.cuda_build_present():
            self._progress(download_id, "CUDA-сборка уже установлена", 100, 0, 0)
        else:
            def on_progress(done: int, total: int | None) -> None:
                pct = min(99, int(done * 100 / max(total or 1, 1)))
                self._progress(
                    download_id,
                    "Скачивание CUDA-сборки llama.cpp…",
                    pct,
                    done / (1024 * 1024),
                    (total or 0) / (1024 * 1024),
                )

            enhancer.download_cuda_build(on_download=on_progress)
        # После замены бинарников llama-server остановлен — поднимаем его заново
        # с -ngl (GPU-оффлоад) и сообщаем в лог, какой задействован backend.
        self._progress(download_id, "Запуск llama-server с GPU…", 99, 0, None)
        if enhancer.start_server():
            self._progress(
                download_id,
                "llama-server готов (CUDA + GPU-оффлоад)",
                100,
                0,
                0,
            )
        else:
            raise RuntimeError(enhancer.last_error or "llama-server не запустился")

    def _progress(
        self,
        download_id: str,
        label: str,
        pct: int,
        done_mb: float,
        total_mb: float | None,
        status: str = "downloading",
    ) -> None:
        self._emit(
            DownloadProgressEvent(
                download_id=download_id,
                label=label,
                pct=pct,
                done_mb=done_mb,
                total_mb=total_mb,
                status=status,
            )
        )
