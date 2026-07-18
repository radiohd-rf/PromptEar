"""Тесты диспетчера событий."""

from unittest.mock import MagicMock

from core.events import LogEvent, ProgressEvent
from ui.handlers import EventDispatcher


def test_register_and_dispatch():
    disp = EventDispatcher()
    handler = MagicMock()
    disp.register(LogEvent, handler)
    event = LogEvent(message="тест")
    disp.dispatch(event)
    handler.assert_called_once_with(event)


def test_dispatch_no_handler():
    disp = EventDispatcher()
    handler = MagicMock()
    disp.register(LogEvent, handler)
    disp.dispatch(ProgressEvent(current=1, total=5, filename="f", eta="10s"))
    handler.assert_not_called()


def test_dispatch_unregistered_type():
    disp = EventDispatcher()
    handler = MagicMock()
    disp.dispatch(LogEvent(message="тест"))
    handler.assert_not_called()
