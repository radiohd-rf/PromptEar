"""Протокол очереди сообщений между воркером и GUI."""

from enum import Enum, auto


class QueueMsg(Enum):
    """Типы сообщений для очереди поток→GUI."""

    LOG = auto()  # str — сообщение в лог
    PROGRESS = auto()  # (current, total, filename, eta)
    TRANSCRIBING = auto()  # str — "Транскрибация: file.mp3"
    WHISPER_PROGRESS = auto()  # dict — детальный прогресс Whisper
    OLLAMA_READY = auto()  # (ollama_ok, model_ok)
    CUDA_INSTALLED = auto()  # bool
    DONE = auto()  # str — финальное сообщение
    ERROR = auto()  # str — сообщение об ошибке
    CANCELLED = auto()  # str — "Остановлено пользователем"
    SET_BUSY = auto()  # bool
