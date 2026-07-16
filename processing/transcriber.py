"""Транскрипция аудио через Whisper."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

from config import WHISPER_MODEL
from utils.protocol import QueueMsg


def ensure_whisper():
    """Проверяет и устанавливает whisper + torch если нужно."""
    if importlib.util.find_spec("whisper") is not None:
        return True

    print("Whisper не установлен. Установка...")
    subprocess.run("pip install openai-whisper", shell=True, capture_output=True, timeout=300)
    if importlib.util.find_spec("whisper") is None:
        print("ОШИБКА: Не удалось установить whisper")
        return False
    return True


ensure_whisper()
import whisper  # noqa: E402


class Transcriber:
    """Класс для транскрипции аудио через Whisper."""

    def __init__(self):
        self._model = None

    def load_model(self, model_name: str = WHISPER_MODEL):
        """Загружает модель Whisper (лениво, один раз)."""
        if self._model is None:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = whisper.load_model(model_name, device=device)

    def transcribe(self, audio_path: Path, queue=None, **kwargs) -> str:
        """Транскрибирует аудиофайл."""
        self.load_model()

        if queue:
            original_stdout = sys.stdout
            original_stderr = sys.stderr
            silent = open("nul", "w") if sys.platform == "win32" else open(os.devnull, "w")  # noqa: SIM115

            class ProgressCapture:
                def __init__(self, original, queue):
                    self.original = original
                    self.queue = queue

                def write(self, text):
                    self.original.write(text)
                    self._parse_progress(text)

                def flush(self):
                    self.original.flush()

                def _parse_progress(self, text):
                    match = re.search(
                        r"(\d+)%\|.*?\|\s*(\d+)/(\d+)\s*\[([^<]*)<([^,]*),\s*([0-9.]+)frames/s\]",
                        text,
                    )
                    if match:
                        percent = int(match.group(1))
                        current = int(match.group(2))
                        total = int(match.group(3))
                        elapsed = match.group(4).strip()
                        remaining = match.group(5).strip()
                        speed = match.group(6)
                        self.queue.put(
                            (
                                QueueMsg.WHISPER_PROGRESS,
                                {
                                    "percent": percent,
                                    "current": current,
                                    "total": total,
                                    "elapsed": elapsed,
                                    "remaining": remaining,
                                    "speed": speed,
                                },
                            )
                        )

            sys.stdout = ProgressCapture(silent, queue)
            sys.stderr = ProgressCapture(original_stderr, queue)

            try:
                result = self._model.transcribe(str(audio_path), **kwargs)  # type: ignore[union-attr]
            finally:
                sys.stdout = original_stdout
                sys.stderr = original_stderr
                silent.close()

            return result.get("text", "").strip()
        else:
            result = self._model.transcribe(str(audio_path), **kwargs)  # type: ignore[union-attr]
            return result.get("text", "").strip()

    def unload(self):
        """Выгружает модель из памяти."""
        self._model = None
