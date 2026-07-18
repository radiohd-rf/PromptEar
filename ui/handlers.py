"""Диспетчер событий для очереди worker → GUI."""

from collections.abc import Callable

from core.events import PipelineEvent


class EventDispatcher:
    """Реестр обработчиков по типу события."""

    def __init__(self) -> None:
        self._handlers: dict[type, Callable[[PipelineEvent], None]] = {}

    def register(self, event_type: type, handler: Callable[[PipelineEvent], None]) -> None:
        self._handlers[event_type] = handler

    def dispatch(self, event: PipelineEvent) -> None:
        handler = self._handlers.get(type(event))
        if handler:
            handler(event)
