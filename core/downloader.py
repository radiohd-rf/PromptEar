"""Фоновые скачивания: модели Whisper, Gemma (GGUF), llama.cpp.

DownloadManager держит ОДИН активный download (см. спека unified-build §6).
Прогресс репортится событиями DownloadProgressEvent / DownloadDoneEvent /
DownloadFailedEvent через emit-колбэк (сервер рассылает их в SSE-канал).
"""

from __future__ import annotations

import contextlib
import re
import threading
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from config import (
    GGUF_URL,
    LLAMA_DIR,
    LLM_MODEL_DIR,
)
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


def _hf_api(repo_id: str) -> str:
    return f"https://huggingface.co/api/models/{repo_id}"


def _hf_file_url(repo_id: str, name: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{name}"


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
                "gemma": "Модель ИИ (Gemma, ~3 ГБ)",
                "llama": "llama.cpp",
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
                model = model or settings_store.load().get("whisper_model", "base")
                self._run_whisper(download_id, model)
            elif kind == "gemma":
                self._run_gemma(download_id)
            elif kind == "llama":
                self._run_llama(download_id)
            else:
                raise RuntimeError(f"Неизвестный тип скачивания: {kind}")
            self._emit(DownloadDoneEvent(download_id))
        except Exception as exc:
            self._emit(DownloadFailedEvent(download_id, str(exc)))
        finally:
            with self._lock:
                self._active = None

    def _run_whisper(self, download_id: str, alias: str) -> None:
        if wm.is_installed(alias):
            # уже установлена — просто переключаем на неё и чистим остальные
            wm.remove_others(alias)
            settings_store.save({"whisper_model": alias})
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
        settings_store.save({"whisper_model": alias})
        size = wm.size_mb(alias)
        self._progress(download_id, f"Модель {alias} готова", 100, size, size)

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
        from config import LLAMA_RELEASE

        server_exe = LLAMA_DIR / "llama-server.exe"
        if server_exe.exists():
            self._progress(download_id, "llama.cpp уже установлен", 100, 0, 0)
            return
        url = (
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-bin-win-cpu-x64.zip"
        )
        zip_path = LLAMA_DIR / "llama.zip"
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        self._progress(download_id, "Скачивание llama.cpp…", 0, 0, None)

        def on_progress(done: int, total: int | None) -> None:
            pct = min(99, int(done * 100 / max(total or 1, 1)))
            done_mb = done / (1024 * 1024)
            total_mb = (total or 0) / (1024 * 1024)
            self._progress(download_id, "Скачивание llama.cpp…", pct, done_mb, total_mb)

        fetch_file(url, zip_path, on_progress=on_progress)
        self._progress(download_id, "Распаковка llama.cpp…", 99, 0, None)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(LLAMA_DIR)
        zip_path.unlink()
        self._progress(download_id, "llama.cpp готов", 100, 0, 0)

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
