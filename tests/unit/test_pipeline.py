"""Тесты пайплайна обработки аудио."""

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, PropertyMock

import pytest

from core.models import AudioFile, PipelineConfig, TranscriptionResult
from core.pipeline import (
    AudioPipeline,
    CleanupStep,
    DetectPreprocessStep,
    EnhanceStep,
    SaveStep,
    TranscribeStep,
)


@pytest.fixture
def audio_file():
    return AudioFile(path=Path("test.wav"))


@pytest.fixture
def config():
    return PipelineConfig()


@pytest.fixture
def emit():
    return MagicMock()


@pytest.fixture
def cancel():
    return Event()


@pytest.fixture
def result(audio_file):
    return TranscriptionResult(audio=audio_file, text="")


# ── DetectPreprocessStep ─────────────────────────────────────────────────────


def test_detect_preprocess_success(result, config, emit, cancel, mocker):
    mocker.patch("core.pipeline.AudioDetector.is_quiet", return_value=False)
    preproc_path = Path("preproc.wav")
    mock_preproc = mocker.patch("core.pipeline.AudioDetector.preprocess", return_value=preproc_path)

    step = DetectPreprocessStep()
    out = step.process(result, config, emit, cancel)

    assert out.audio.preprocessed is True
    assert out.audio.preprocessed_path == Path("preproc.wav")
    assert out.audio.temp_path == Path("preproc.wav")
    mock_preproc.assert_called_once_with(Path("test.wav"), quiet=False)


def test_detect_preprocess_quiet(result, config, emit, cancel, mocker):
    mocker.patch("core.pipeline.AudioDetector.is_quiet", return_value=True)
    preproc_path = Path("preproc.wav")
    mock_preproc = mocker.patch("core.pipeline.AudioDetector.preprocess", return_value=preproc_path)

    step = DetectPreprocessStep()
    step.process(result, config, emit, cancel)

    emit.assert_any_call(mocker.ANY)
    mock_preproc.assert_called_once_with(Path("test.wav"), quiet=True)


def test_detect_preprocess_exception_fallback(result, config, emit, cancel, mocker):
    mocker.patch("core.pipeline.AudioDetector.is_quiet", return_value=False)
    mocker.patch("core.pipeline.AudioDetector.preprocess", side_effect=RuntimeError("fail"))

    step = DetectPreprocessStep()
    out = step.process(result, config, emit, cancel)

    assert out.audio.preprocessed_path == Path("test.wav")
    assert out.audio.temp_path is None


# ── TranscribeStep ───────────────────────────────────────────────────────────


def test_transcribe_step_calls_transcriber(result, config, emit, cancel):
    transcriber = MagicMock()
    transcriber.transcribe.return_value = "распознанный текст"
    result.audio.preprocessed_path = Path("test.wav")

    step = TranscribeStep(transcriber)
    out = step.process(result, config, emit, cancel)

    assert out.text == "распознанный текст"
    transcriber.transcribe.assert_called_once_with(
        Path("test.wav"), language="ru", beam_size=5, vad_filter=True
    )


def test_transcribe_step_initial_prompt(result, config, emit, cancel):
    transcriber = MagicMock()
    transcriber.transcribe.return_value = "текст"
    result.audio.preprocessed_path = Path("test.wav")
    config.initial_prompt = "подсказка"

    step = TranscribeStep(transcriber)
    step.process(result, config, emit, cancel)

    transcriber.transcribe.assert_called_once_with(
        Path("test.wav"), language="ru", beam_size=5, vad_filter=True, initial_prompt="подсказка"
    )


# ── SaveStep ─────────────────────────────────────────────────────────────────


def test_save_step_docx(result, config, emit, cancel, mocker):
    mock_save_docx = mocker.patch("core.pipeline.save_docx")
    result.text = "текст для сохранения"
    result.audio.path = Path("test.wav")
    config.output_format = "docx"

    step = SaveStep()
    out = step.process(result, config, emit, cancel)

    assert out.output_path == Path("test.docx")
    mock_save_docx.assert_called_once_with(Path("test.docx"), "текст для сохранения")


def test_save_step_txt(result, config, emit, cancel, mocker):
    mock_save_txt = mocker.patch("core.pipeline.save_txt")
    result.text = "текст для сохранения"
    result.audio.path = Path("test.wav")
    config.output_format = "txt"

    step = SaveStep()
    step.process(result, config, emit, cancel)

    mock_save_txt.assert_called_once_with(Path("test.txt"), "текст для сохранения")


def test_save_step_empty_text(result, config, emit, cancel, mocker):
    mock_save_docx = mocker.patch("core.pipeline.save_docx")
    result.text = ""
    result.audio.path = Path("test.wav")

    step = SaveStep()
    step.process(result, config, emit, cancel)

    mock_save_docx.assert_not_called()
    assert result.output_path is None


# ── CleanupStep ──────────────────────────────────────────────────────────────


def test_cleanup_removes_temp(result, config, emit, cancel):
    tmp = Path("temp.wav")
    tmp.touch()
    result.audio.temp_path = tmp

    step = CleanupStep()
    step.process(result, config, emit, cancel)

    assert not tmp.exists()
    tmp.unlink(missing_ok=True)


def test_cleanup_no_temp(result, config, emit, cancel):
    result.audio.temp_path = None

    step = CleanupStep()
    step.process(result, config, emit, cancel)


# ── EnhanceStep ──────────────────────────────────────────────────────────────


def test_enhance_step_calls_multi_pass(result, config, emit, cancel):
    enhancer = MagicMock()
    enhancer.enhance_multi_pass.return_value = "улучшенный текст"
    result.text = "исходный текст"
    config.qwen_available = True

    step = EnhanceStep(enhancer)
    out = step.process(result, config, emit, cancel)

    assert out.text == "улучшенный текст"
    enhancer.enhance_multi_pass.assert_called_once()


def test_enhance_step_qwen_unavailable(result, config, emit, cancel):
    enhancer = MagicMock()
    result.text = "исходный текст"
    config.qwen_available = False

    step = EnhanceStep(enhancer)
    step.process(result, config, emit, cancel)

    enhancer.enhance_multi_pass.assert_not_called()


def test_enhance_step_empty_text(result, config, emit, cancel):
    enhancer = MagicMock()
    result.text = ""
    config.qwen_available = True

    step = EnhanceStep(enhancer)
    step.process(result, config, emit, cancel)

    enhancer.enhance_multi_pass.assert_not_called()


# ── AudioPipeline.run ────────────────────────────────────────────────────────


def test_pipeline_run_all_steps(mocker):
    mock_step = MagicMock()
    mock_step.process.side_effect = lambda r, c, e, cl: r
    type(mock_step).name = PropertyMock(return_value="mock")

    mock_transcriber = MagicMock()
    mock_enhancer = MagicMock()

    mocker.patch("core.pipeline.get_torch_device", return_value="cpu")

    pipeline = AudioPipeline(steps=[mock_step])
    files = [AudioFile(path=Path("f1.wav")), AudioFile(path=Path("f2.wav"))]
    config = PipelineConfig()
    emit = MagicMock()
    cancel = Event()

    pipeline.run(files, config, emit, cancel, transcriber=mock_transcriber, enhancer=mock_enhancer)

    assert mock_step.process.call_count == 2
    emit.assert_any_call(mocker.ANY)


def test_pipeline_run_cancelled(mocker):
    mock_step = MagicMock()
    type(mock_step).name = PropertyMock(return_value="mock")

    mocker.patch("core.pipeline.get_torch_device", return_value="cpu")

    pipeline = AudioPipeline(steps=[mock_step])
    files = [AudioFile(path=Path("f1.wav"))]
    config = PipelineConfig()
    emit = MagicMock()
    cancel = Event()
    cancel.set()

    pipeline.run(files, config, emit, cancel, transcriber=MagicMock(), enhancer=MagicMock())

    mock_step.process.assert_not_called()
    emit.assert_any_call(mocker.ANY)
