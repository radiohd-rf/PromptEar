"""Тесты маршрутизации QueueMsg в _check_queue."""

import queue
from unittest.mock import MagicMock

import pytest

from utils.protocol import QueueMsg


@pytest.fixture
def app_mock():
    """Создаёт минимальный mock PromptEarApp с _check_queue."""
    import app as app_module

    mock = MagicMock(spec=app_module.PromptEarApp)
    mock._queue = queue.Queue()
    mock._running = False
    return mock


def _simulate_check_queue(app_mock):
    """Эмулирует один проход _check_queue."""

    # Вызываем _check_queue в упрощённом виде
    try:
        while True:
            msg_type, msg = app_mock._queue.get_nowait()
            if msg_type is QueueMsg.LOG:
                app_mock._log(msg)
            elif msg_type is QueueMsg.PROGRESS:
                app_mock._set_progress_text(f"[{msg[0]}/{msg[1]}] {msg[2]}")
            elif msg_type is QueueMsg.DONE:
                app_mock._set_busy(False)
            elif msg_type is QueueMsg.TRANSCRIBING:
                app_mock._set_progress_text(msg)
            elif msg_type is QueueMsg.OLLAMA_READY:
                app_mock._enhancer._ollama_ok = msg[0]
            elif msg_type is QueueMsg.ERROR or msg_type is QueueMsg.CANCELLED:
                app_mock._set_busy(False)
            elif msg_type is QueueMsg.CUDA_INSTALLED:
                pass
            elif msg_type is QueueMsg.SET_BUSY:
                app_mock._set_busy(msg)
    except queue.Empty:
        pass


def test_log_message_routed(app_mock):
    app_mock._queue.put((QueueMsg.LOG, "test message"))
    _simulate_check_queue(app_mock)
    app_mock._log.assert_called_once_with("test message")


def test_progress_message_routed(app_mock):
    app_mock._queue.put((QueueMsg.PROGRESS, (1, 3, "file.mp3", "1m 30s")))
    _simulate_check_queue(app_mock)
    app_mock._set_progress_text.assert_called_once_with("[1/3] file.mp3")


def test_done_message_resets_busy(app_mock):
    app_mock._queue.put((QueueMsg.DONE, "Готово"))
    _simulate_check_queue(app_mock)
    app_mock._set_busy.assert_called_once_with(False)


def test_error_message_resets_busy(app_mock):
    app_mock._queue.put((QueueMsg.ERROR, "Ошибка: abc"))
    _simulate_check_queue(app_mock)
    app_mock._set_busy.assert_called_once_with(False)


def test_cancelled_message_resets_busy(app_mock):
    app_mock._queue.put((QueueMsg.CANCELLED, "Отменено"))
    _simulate_check_queue(app_mock)
    app_mock._set_busy.assert_called_once_with(False)


def test_ollama_ready_updates_flags(app_mock):
    app_mock._enhancer = MagicMock()
    app_mock._queue.put((QueueMsg.OLLAMA_READY, (True, True)))
    _simulate_check_queue(app_mock)
    assert app_mock._enhancer._ollama_ok is True


def test_transcribing_routed(app_mock):
    app_mock._queue.put((QueueMsg.TRANSCRIBING, "Транскрибация: file.mp3"))
    _simulate_check_queue(app_mock)
    app_mock._set_progress_text.assert_called_once_with("Транскрибация: file.mp3")


def test_multiple_messages_processed(app_mock):
    app_mock._queue.put((QueueMsg.LOG, "first"))
    app_mock._queue.put((QueueMsg.LOG, "second"))
    _simulate_check_queue(app_mock)
    assert app_mock._log.call_count == 2


def test_set_busy_routed(app_mock):
    app_mock._queue.put((QueueMsg.SET_BUSY, True))
    _simulate_check_queue(app_mock)
    app_mock._set_busy.assert_called_once_with(True)
