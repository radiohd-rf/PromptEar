"""Тесты транскрипции через faster-whisper."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from processing.transcriber import Transcriber


@pytest.fixture
def transcriber():
    return Transcriber()


def _make_segments(text: str):
    seg = MagicMock()
    seg.text = text
    return [seg], MagicMock()


@pytest.fixture
def mock_model(mocker):
    model = MagicMock()
    model.transcribe.return_value = _make_segments("распознанный текст")
    mocker.patch("faster_whisper.WhisperModel", return_value=model)
    return model


@pytest.fixture
def mock_load_model(mocker):
    model = MagicMock()
    model.transcribe.return_value = _make_segments("распознанный текст")
    patched = mocker.patch("faster_whisper.WhisperModel", return_value=model)
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
    mock_model.transcribe.assert_called_once_with(
        str(path), language="ru", beam_size=5, vad_filter=True
    )


def test_transcribe_kwargs_passed(transcriber, mock_model):
    path = Path("test.wav")
    transcriber.transcribe(path, initial_prompt="тест", language="ru")
    mock_model.transcribe.assert_called_once_with(
        str(path), language="ru", beam_size=5, vad_filter=True, initial_prompt="тест"
    )


def test_unload(transcriber, mock_model):
    transcriber.load_model()
    assert transcriber._model is not None
    transcriber.unload()
    assert transcriber._model is None


# ── ensure_faster_whisper ────────────────────────────────────────────────────


def test_ensure_faster_whisper_already_installed(mocker):
    mocker.patch("importlib.util.find_spec", return_value=MagicMock())
    from processing.transcriber import ensure_faster_whisper
    assert ensure_faster_whisper() is True


def test_ensure_faster_whisper_needs_install(mocker):
    find_spec = mocker.patch("importlib.util.find_spec")
    find_spec.side_effect = [None, MagicMock()]
    mock_run = mocker.patch("processing.transcriber.subprocess.run")

    from processing.transcriber import ensure_faster_whisper
    result = ensure_faster_whisper()
    assert result is True
    mock_run.assert_called_once()
