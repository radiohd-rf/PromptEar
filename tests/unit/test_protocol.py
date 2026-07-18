"""Тесты протокола очереди."""

from utils.protocol import QueueMsg


def test_all_types_unique():
    values = [m.value for m in QueueMsg]
    assert len(values) == len(set(values)), "Duplicate QueueMsg values"


def test_log_exists():
    assert QueueMsg.LOG is not None


def test_done_exists():
    assert QueueMsg.DONE is not None


def test_error_exists():
    assert QueueMsg.ERROR is not None


def test_cancelled_exists():
    assert QueueMsg.CANCELLED is not None


def test_progress_exists():
    assert QueueMsg.PROGRESS is not None


def test_transcribing_exists():
    assert QueueMsg.TRANSCRIBING is not None


def test_whisper_progress_exists():
    assert QueueMsg.WHISPER_PROGRESS is not None


def test_ollama_ready_exists():
    assert QueueMsg.OLLAMA_READY is not None


def test_cuda_installed_exists():
    assert QueueMsg.CUDA_INSTALLED is not None


def test_set_busy_exists():
    assert QueueMsg.SET_BUSY is not None


def test_ten_messages_total():
    assert len(QueueMsg) == 10


def test_members_are_enum():
    for m in QueueMsg:
        assert isinstance(m, QueueMsg)
