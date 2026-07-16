"""Тесты детектора тихого аудио и предобработки."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import QUIET_THRESHOLD_DB
from processing.detector import detect_quiet_audio, preprocess_audio


def make_ffmpeg_stderr(mean_db: float) -> str:
    return f"""
[Parsed_volumedetect_0 @ ...] mean_volume: {mean_db:.1f} dB
[Parsed_volumedetect_0 @ ...] max_volume: -5.0 dB
"""


def test_detect_loud(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stderr=make_ffmpeg_stderr(-10.0), stdout="")
    assert detect_quiet_audio(Path("test.wav")) is False


def test_detect_quiet(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stderr=make_ffmpeg_stderr(-30.0), stdout="")
    assert detect_quiet_audio(Path("test.wav")) is True


def test_detect_boundary(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.return_value = MagicMock(
        returncode=0, stderr=make_ffmpeg_stderr(QUIET_THRESHOLD_DB), stdout=""
    )
    assert detect_quiet_audio(Path("test.wav")) is (QUIET_THRESHOLD_DB < -20.0)


def test_detect_error(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.side_effect = Exception("ffmpeg not found")
    assert detect_quiet_audio(Path("test.wav")) is False


def test_detect_no_mean_volume(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stderr="no data", stdout="")
    assert detect_quiet_audio(Path("test.wav")) is False


def test_preprocess_ok(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    result = preprocess_audio(Path("test.wav"))
    assert isinstance(result, Path)
    assert result.suffix == ".wav"
    mock_run.assert_called_once()


def test_preprocess_fail(mocker):
    mock_run = mocker.patch("processing.detector.subprocess.run")
    mock_run.side_effect = RuntimeError("ffmpeg error")
    with pytest.raises(RuntimeError):
        preprocess_audio(Path("test.wav"))
