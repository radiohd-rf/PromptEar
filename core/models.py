"""Типизированные модели предметной области."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AudioFile:
    """Аудиофайл для транскрибации."""

    path: Path
    original_path: Path | None = None
    preprocessed: bool = False
    preprocessed_path: Path | None = None
    temp_path: Path | None = None


@dataclass
class PipelineConfig:
    """Конфигурация пайплайна обработки одного файла."""

    output_dir: Path | None = None
    output_format: str = "docx"
    multi_pass: bool = False
    initial_prompt: str | None = None
    qwen_available: bool = False


@dataclass
class TranscriptionResult:
    """Результат транскрибации файла."""

    audio: AudioFile
    text: str
    duration_sec: float = 0.0
    output_path: Path | None = None
    preview: str = ""
    whisper_confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.preview:
            self.preview = self.text[:100] + "..." if len(self.text) > 100 else self.text
