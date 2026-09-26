"""Типизированные события для очереди GUI."""

from dataclasses import dataclass
from enum import Enum, auto


class QueueMsg(Enum):
    """Типы сообщений (обратная совместимость)."""

    LOG = auto()
    PROGRESS = auto()
    TRANSCRIBING = auto()
    LLM_READY = auto()
    CUDA_INSTALLED = auto()
    DONE = auto()
    ERROR = auto()
    CANCELLED = auto()
    SET_BUSY = auto()


class PipelineEvent:
    """Базовый класс для типизированных событий."""


@dataclass
class LogEvent(PipelineEvent):
    message: str


@dataclass
class ProgressEvent(PipelineEvent):
    current: int
    total: int
    filename: str
    eta: str


@dataclass
class TranscribingEvent(PipelineEvent):
    message: str


@dataclass
class DraftEvent(PipelineEvent):
    """Живой черновик транскрибации (сырой текст по мере распознавания)."""

    text: str
    final: bool = False
    filename: str = ""


@dataclass
class EnhancingEvent(PipelineEvent):
    """Прогресс многопроходного улучшения."""

    active_pass: int
    total_passes: int


@dataclass
class EnhancingStreamEvent(PipelineEvent):
    """Накопленный текст прохода по мере генерации («модель печатает»)."""

    filename: str
    text: str
    active_pass: int = 0
    final: bool = False


@dataclass
class FileStatusEvent(PipelineEvent):
    """Изменение статуса файла в очереди."""

    filename: str
    status: str  # queued|processing|transcribing|enhancing|done|skipped
    audio_ok: bool = False  # аудио готово для плеера (опубликовано в кэш)


@dataclass
class SkippedEvent(PipelineEvent):
    """Файл пропущен пользователем (обработан наполовину)."""

    filename: str
    message: str = ""


@dataclass
class ResultEvent(PipelineEvent):
    """Финальный (улучшенный) текст файла."""

    text: str
    filename: str


@dataclass
class LlmReadyEvent(PipelineEvent):
    llm_ok: bool
    model_ok: bool


@dataclass
class CudaInstalledEvent(PipelineEvent):
    success: bool


@dataclass
class DoneEvent(PipelineEvent):
    message: str


@dataclass
class ErrorEvent(PipelineEvent):
    message: str


@dataclass
class CancelledEvent(PipelineEvent):
    message: str


@dataclass
class SetBusyEvent(PipelineEvent):
    busy: bool


@dataclass
class DownloadProgressEvent(PipelineEvent):
    """Прогресс фонового скачивания (модель/Gemma/llama.cpp).

    total_mb=None — размер неизвестен (нет Content-Length).
    """

    download_id: str
    label: str
    pct: int
    done_mb: float
    total_mb: float | None = None
    status: str = "downloading"


@dataclass
class DownloadDoneEvent(PipelineEvent):
    download_id: str


@dataclass
class DownloadFailedEvent(PipelineEvent):
    download_id: str
    error: str
