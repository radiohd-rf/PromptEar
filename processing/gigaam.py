"""Транскрибация через GigaAM v3 (transformers + torch).

Используется как движок распознавания для русского (ru-only). Длинные файлы
режутся на чанки ≤20 с через ffmpeg — официальный transcribe_longform требует
gated-модель pyannote и HF-токен, поэтому для оффлайн-работы используем своё
чанкование (таймкоды получаются с точностью до границы чанка).

torch + pyannote.audio не обязательны для установки приложения: ставятся по
запросу при первом выборе GigaAM (kind=gigaam_deps в DownloadManager).
"""

from __future__ import annotations

import contextlib
import importlib.util
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from typing import Any
from unittest import mock

from config import BASE_DIR, MODEL_IDLE_CHECK_SEC, MODEL_IDLE_TIMEOUT_SEC, TEMP_DIR
from core import gigaam_models as gm
from core.models import Segment
from utils.logger import get_logger

CHUNK_SECONDS = 20
LONGFORM_LIMIT = 23.0  # transcribe() падает выше ~25 с — берём с запасом

_engine_lock = threading.Lock()
_engines: dict[tuple[str, str], Any] = {}
# Метки последнего использования (ключ → monotonic) и ключи в работе.
# Сторож выгружает движки после MODEL_IDLE_TIMEOUT_SEC простоя.
_engine_last_used: dict[tuple[str, str], float] = {}
_engine_inflight: set[tuple[str, str]] = set()
_gigaam_idle_watcher_started = False


def gigaam_deps_ok() -> bool:
    """Установлены ли зависимости GigaAM (torch + pyannote)."""
    return (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("pyannote") is not None
    )


def get_device(use_gpu: bool) -> str:
    """Реальный device для GigaAM: 'cuda' или 'cpu'."""
    if not use_gpu:
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _ensure_ffmpeg_in_path() -> None:
    """Внутренний load_audio GigaAM вызывает 'ffmpeg' из PATH."""
    bundled = BASE_DIR / "ffmpeg.exe"
    if bundled.exists():
        cur = os.environ.get("PATH", "")
        if str(BASE_DIR) not in cur.split(os.pathsep):
            os.environ["PATH"] = str(BASE_DIR) + os.pathsep + cur


@contextlib.contextmanager
def _hidden_child_consoles() -> Iterator[None]:
    """Гасит вспышки консолей от вендорного кода.

    load_audio() из modeling_gigaam.py спавнит ffmpeg через голый
    subprocess.run БЕЗ CREATE_NO_WINDOW — каждое окно вспыхивает консолью
    (по одному на чанк). Подменяем Popen на время транскрибации, добавляя
    флаг (только Windows; явно заданные флаги уважаем и дополняем).
    """
    if os.name != "nt":
        yield
        return
    orig_popen = subprocess.Popen

    def _popen_no_window(*args: Any, **kwargs: Any) -> Any:
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | (
            subprocess.CREATE_NO_WINDOW
        )
        return orig_popen(*args, **kwargs)

    with mock.patch.object(subprocess, "Popen", new=_popen_no_window):
        yield


def _load_engine(variant: str, device: str) -> Any:
    key = (variant, device)
    # Важно: _ensure_idle_watcher() тоже берёт _engine_lock — вызывать его
    # можно только ВНЕ with-блока, иначе self-deadlock обычного Lock
    # (висли все файлы начиная со второго: первое обращение — miss-ветка,
    # все последующие — hit-ветка с вложенным захватом).
    with _engine_lock:
        cached = _engines.get(key)
        if cached is not None:
            _engine_last_used[key] = time.monotonic()
    if cached is not None:
        _ensure_idle_watcher()
        return cached
    import torch
    from transformers import AutoModel

    _ensure_ffmpeg_in_path()
    local_dir = str(gm.model_dir(variant))
    model = AutoModel.from_pretrained(local_dir, trust_remote_code=True)
    model = model.to(torch.device(device)).eval()
    with _engine_lock:
        _engines[key] = model
        _engine_last_used[key] = time.monotonic()
    _ensure_idle_watcher()
    return model


def _unload_key(key: tuple[str, str]) -> bool:
    """Убирает движок из кэша; True если что-то выгружено."""
    with _engine_lock:
        if key not in _engines:
            return False
        del _engines[key]
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return True


def unload_engine(variant: str) -> None:
    """Выгружает движок (при смене варианта), освобождая память/VRAM."""
    with _engine_lock:
        keys = [k for k in _engines if k[0] == variant]
    for k in keys:
        _unload_key(k)


def _ensure_idle_watcher() -> None:
    """Запускает сторожа простоя (один раз за процесс)."""
    global _gigaam_idle_watcher_started
    with _engine_lock:
        if _gigaam_idle_watcher_started:
            return
        _gigaam_idle_watcher_started = True
    threading.Thread(target=_idle_watcher, name="gigaam-idle", daemon=True).start()


def _idle_watcher() -> None:
    """Фон: выгружает движки после MODEL_IDLE_TIMEOUT_SEC простоя."""
    while True:
        time.sleep(MODEL_IDLE_CHECK_SEC)
        # Сторож не должен умирать.
        with contextlib.suppress(Exception):
            _idle_check()


def _idle_check() -> None:
    """Одна проверка сторожа (вынесена для тестируемости)."""
    now = time.monotonic()
    with _engine_lock:
        stale = [
            (k, ts)
            for k, ts in _engine_last_used.items()
            if k not in _engine_inflight and now - ts >= MODEL_IDLE_TIMEOUT_SEC
        ]
    for key, ts in stale:
        if _unload_key(key):
            with _engine_lock:
                # За время выгрузки движок могли перезагрузить — чужую
                # свежую метку не трогаем, иначе утечка (метки нет в словаре
                # → ключ больше никогда не проверяется).
                if _engine_last_used.get(key) == ts:
                    _engine_last_used.pop(key, None)
            variant, device = key
            mins = MODEL_IDLE_TIMEOUT_SEC // 60
            get_logger().info(f"GigaAM {variant}/{device} выгружен после {mins} мин простоя")


def _wav_duration(path: Path, ffmpeg: str) -> float:
    result = subprocess.run(
        [ffmpeg, "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
    if not match:
        return 0.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _chunks(duration: float, ffmpeg: str, src: Path, tmp_dir: Path) -> list[Path]:
    """Режет аудио на 16kHz-mono чанки ≤20 с и возвращает их пути."""
    chunks: list[Path] = []
    count = max(1, math.ceil(duration / CHUNK_SECONDS))
    for i in range(count):
        start = i * CHUNK_SECONDS
        chunk = tmp_dir / f"ggam-{uuid.uuid4().hex[:8]}.wav"
        cmd = [
            ffmpeg,
            "-ss", f"{start}",
            "-t", f"{CHUNK_SECONDS}",
            "-i", str(src),
            "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le",
            "-y",
            str(chunk),
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if res.returncode != 0 or not chunk.exists():
            for c in chunks:
                c.unlink(missing_ok=True)
            # Хвост stderr, а не начало: в начале всегда баннер версии ffmpeg.
            err = (res.stderr or "").strip().splitlines()
            raise RuntimeError(f"Ошибка нарезки аудио ffmpeg: {' | '.join(err[-3:])}")
        chunks.append(chunk)
    return chunks


def transcribe_with_segments(
    audio_path: Path,
    variant: str,
    use_gpu: bool = False,
    cancel: Event | None = None,
    on_segment=None,
    temp_dir: Path | None = None,
    **kwargs: Any,
) -> tuple[str, list[Segment], float]:
    """Транскрибирует аудио GigaAM и возвращает (текст, сегменты, длительность).

    Экспортируемый интерфейс совместим с Transcriber.transcribe_with_segments
    (whisper-специфичные kwargs игнорируются). Сегменты — по границам чанков
    (настоящих слов таймкодов у GigaAM нет). cancel проверяется между чанками.
    """
    if not gigaam_deps_ok():
        raise RuntimeError(
            "GigaAM не установлен: требуются torch и pyannote. "
            "Воспользуйтесь выбором движка в окне запуска."
        )
    # Свой подкаталог на вызов: finally делает rmtree — чистить общий
    # корень TEMP_DIR нельзя (там чужие задачи и файлы).
    tmp_dir = (temp_dir or TEMP_DIR) / f"gigaam-{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    import torch

    from utils.extract_audio import get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    duration = _wav_duration(audio_path, ffmpeg)
    if duration <= 0:
        raise RuntimeError(
            "Не удалось определить длительность аудио — файл, вероятно, "
            "повреждён или пуст. Проверьте файл и попробуйте ещё раз."
        )
    get_logger().info(f"  Длительность аудио: {duration:.0f}с")
    device = get_device(use_gpu)
    engine = _load_engine(variant, device)
    key = (variant, device)

    parts: list[str] = []
    segments: list[Segment] = []

    def run_single(wav: Path, start: float, end: float) -> None:
        with _engine_lock:
            _engine_last_used[key] = time.monotonic()
        with torch.inference_mode():
            text = engine.transcribe(str(wav))
        seg = Segment(start=start, end=end, text=text)
        parts.append(text)
        segments.append(seg)
        if on_segment is not None:
            on_segment(list(segments))

    with _engine_lock:
        _engine_inflight.add(key)
    # Вендорный load_audio внутри engine.transcribe спавнит ffmpeg без
    # CREATE_NO_WINDOW — прячем консоли на время всей транскрибации.
    with _hidden_child_consoles():
        try:
            if duration <= LONGFORM_LIMIT:
                run_single(audio_path, 0.0, duration)
            else:
                chunks = _chunks(duration, ffmpeg, audio_path, tmp_dir)
                total = len(chunks)
                get_logger().info(f"  Нарезка: {total} чанков по ≤{CHUNK_SECONDS}с")
                for i, chunk in enumerate(chunks):
                    if cancel is not None and cancel.is_set():
                        break
                    start = i * CHUNK_SECONDS
                    end = min(start + CHUNK_SECONDS, duration)
                    run_single(chunk, start, end)
                    if (i + 1) % 10 == 0 or i + 1 == total:
                        get_logger().info(f"  Чанк {i + 1}/{total}...")
                    chunk.unlink(missing_ok=True)
        finally:
            with _engine_lock:
                _engine_inflight.discard(key)
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
    text = " ".join(parts).strip()
    if not segments:
        segments = [Segment(start=0.0, end=duration, text=text)]
    return text, segments, duration
