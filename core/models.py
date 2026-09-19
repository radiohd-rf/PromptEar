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
    skipped: bool = False


@dataclass
class Segment:
    """Сегмент транскрипции с таймкодами."""

    start: float
    end: float
    text: str


# Режимы улучшения
ENHANCE_NONE = "none"     # только транскрибация, без ИИ
ENHANCE_AUTO = "auto"     # улучшение сразу после транскрибации
ENHANCE_ASK = "ask"       # после транскрибации — кнопка «Улучшить с ИИ»
ENHANCE_MODES = (ENHANCE_NONE, ENHANCE_AUTO, ENHANCE_ASK)


@dataclass
class PipelineConfig:
    """Конфигурация пайплайна обработки одного файла."""

    output_dir: Path | None = None
    output_format: str = "docx"
    multi_pass: bool = False
    initial_prompt: str | None = None
    llm_available: bool = False
    enhance_mode: str = ENHANCE_AUTO
    temp_dir: Path | None = None


@dataclass
class TranscriptionResult:
    """Результат транскрибации файла."""

    audio: AudioFile
    text: str
    duration_sec: float = 0.0
    output_path: Path | None = None
    preview: str = ""
    whisper_confidence: float | None = None
    segments: list[Segment] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.preview:
            self.preview = self.text[:100] + "..." if len(self.text) > 100 else self.text
