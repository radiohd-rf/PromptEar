"""Транскрипция аудио через faster-whisper (CTranslate2)."""

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

from config import WHISPER_MODEL


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


ensure_faster_whisper()


class Transcriber:
    """Класс для транскрипции аудио через faster-whisper с фоновой загрузкой модели."""

    def __init__(self):
        self._model = None
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

    def transcribe(self, audio_path: Path, **kwargs) -> str:
        """Транскрибирует аудиофайл через faster-whisper. Ждёт загрузку модели если нужно."""
        self.load_model()

        language = kwargs.pop("language", "ru")
        beam_size = kwargs.pop("beam_size", 5)
        vad_filter = kwargs.pop("vad_filter", True)

        segments, _info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            **kwargs,
        )
        return " ".join(seg.text for seg in segments)

    def unload(self):
        """Выгружает модель из памяти."""
        self._model = None
