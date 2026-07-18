"""Тесты транскрипции через Whisper."""

import queue
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from processing.transcriber import Transcriber


@pytest.fixture
def transcriber():
    return Transcriber()


@pytest.fixture
def mock_model(mocker):
    model = MagicMock()
    model.transcribe.return_value = {"text": "  распознанный текст  "}
    mocker.patch("whisper.load_model", return_value=model)
    return model


@pytest.fixture
def mock_load_model(mocker):
    model = MagicMock()
    model.transcribe.return_value = {"text": "  распознанный текст  "}
    patched = mocker.patch("whisper.load_model", return_value=model)
    return patched


def test_load_model_lazy(transcriber, mock_load_model):
    assert transcriber._model is None
    transcriber.load_model()
    assert transcriber._model is not None
    # Повторный вызов не пересоздаёт модель
    transcriber.load_model()
    mock_load_model.assert_called_once()


def test_transcribe_calls_whisper(transcriber, mock_model):
    path = Path("test.wav")
    result = transcriber.transcribe(path, language="ru")
    assert result == "распознанный текст"
    mock_model.transcribe.assert_called_once_with(str(path), language="ru")


def test_transcribe_with_queue(transcriber, mock_model):
    q: queue.Queue = queue.Queue()
    path = Path("test.wav")
    result = transcriber.transcribe(path, queue=q, language="ru")
    assert result == "распознанный текст"


def test_transcribe_kwargs_passed(transcriber, mock_model):
    path = Path("test.wav")
    kwargs = dict(initial_prompt="тест", language="ru")
    transcriber.transcribe(path, **kwargs)
    mock_model.transcribe.assert_called_once_with(str(path), **kwargs)


def test_unload(transcriber, mock_model):
    transcriber.load_model()
    assert transcriber._model is not None
    transcriber.unload()
    assert transcriber._model is None


def test_whisper_progress_parsing():
    """Проверяет, что regex корректно парсит stdout Whisper."""
    # симулируем stdout Whisper
    line = " 50%|█████     | 5/10 [00:30<00:30,  5.0frames/s]"
    # regex из transcriber.py
    import re

    match = re.search(
        r"(\d+)%\|.*?\|\s*(\d+)/(\d+)\s*\[([^<]*)<([^,]*),\s*([0-9.]+)frames/s\]", line
    )
    assert match is not None
    assert match.group(1) == "50"
    assert match.group(2) == "5"
    assert match.group(3) == "10"
    assert "00:30" in match.group(4)
    assert "5.0" in match.group(6)


def test_whisper_progress_regex_edge_cases():
    import re

    # 100%, широкие пробелы
    line = "100%|██████████| 10/10 [01:00<00:00, 10.0frames/s]"
    match = re.search(
        r"(\d+)%\|.*?\|\s*(\d+)/(\d+)\s*\[([^<]*)<([^,]*),\s*([0-9.]+)frames/s\]", line
    )
    assert match is not None
    assert match.group(1) == "100"
