"""Транскрипция аудио через faster-whisper (CTranslate2)."""

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from threading import Event
from typing import Any

from config import WHISPER_MODEL
from core.models import Segment


def ensure_faster_whisper():
    """Проверяет и устанавливает faster-whisper если нужно."""
    if importlib.util.find_spec("faster_whisper") is not None:
        return True

    print("faster-whisper не установлен. Установка...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "faster-whisper"],
        timeout=300,
    )
    if importlib.util.find_spec("faster_whisper") is None:
        print("ОШИБКА: Не удалось установить faster-whisper")
        return False
    return True


class Transcriber:
    """Класс для транскрипции аудио через faster-whisper с фоновой загрузкой модели."""

    def __init__(self):
        self._model: Any = None
        self._lock = threading.Lock()

    def load_model(self, model_name: str = WHISPER_MODEL) -> None:
        """Загружает модель Whisper (лениво, один раз). Блокирует до готовности."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is None:
                import torch
                from faster_whisper import WhisperModel

                device = "cuda" if torch.cuda.is_available() else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                self._model = WhisperModel(
                    model_name, device=device, compute_type=compute_type
                )

    def load_model_async(self, model_name: str = WHISPER_MODEL) -> None:
        """Запускает фоновую загрузку модели. Не блокирует."""
        if self._model is not None:
            return
        threading.Thread(target=self.load_model, args=(model_name,), daemon=True).start()

    def transcribe(self, audio_path: Path, cancel: Event | None = None, **kwargs) -> str:
        """Транскрибирует аудиофайл через faster-whisper. Ждёт загрузку модели если нужно."""
        text, _segments, _duration = self.transcribe_with_segments(
            audio_path, cancel=cancel, **kwargs
        )
        return text

    def transcribe_with_segments(
        self,
        audio_path: Path,
        cancel: Event | None = None,
        on_segment=None,
        **kwargs,
    ) -> tuple[str, list[Segment], float]:
        """Транскрибирует аудио и возвращает (текст, сегменты с таймкодами, длительность).

        on_segment вызывается с накопленным сырым текстом по мере распознавания
        (для живого черновика в UI).
        """
        self.load_model()

        language = kwargs.pop("language", "ru")
        beam_size = kwargs.pop("beam_size", 5)
        vad_filter = kwargs.pop("vad_filter", True)

        segments, info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            **kwargs,
        )
        parts: list[str] = []
        raw_segments: list[Segment] = []
        accumulated: list[str] = []
        for seg in segments:
            if cancel is not None and cancel.is_set():
                break
            parts.append(seg.text)
            raw_segments.append(Segment(start=seg.start, end=seg.end, text=seg.text))
            accumulated.append(seg.text)
            if on_segment is not None:
                on_segment(" ".join(accumulated))
        return " ".join(parts), raw_segments, float(getattr(info, "duration", 0.0) or 0.0)

    def unload(self):
        """Выгружает модель из памяти."""
        self._model = None
