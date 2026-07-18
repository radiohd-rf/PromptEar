"""Типизированные события для очереди GUI."""

from dataclasses import dataclass
from enum import Enum, auto


class QueueMsg(Enum):
    """Типы сообщений (обратная совместимость)."""

    LOG = auto()
    PROGRESS = auto()
    TRANSCRIBING = auto()
    WHISPER_PROGRESS = auto()
    OLLAMA_READY = auto()
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
class WhisperProgressEvent(PipelineEvent):
    percent: int
    current: int
    total: int
    elapsed: str
    remaining: str
    speed: str


@dataclass
class OllamaReadyEvent(PipelineEvent):
    ollama_ok: bool
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
