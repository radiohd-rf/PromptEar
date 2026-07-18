"""Тесты моделей предметной области."""

from pathlib import Path

from core.models import AudioFile, PipelineConfig, TranscriptionResult


def test_audio_file_defaults():
    af = AudioFile(path=Path("test.wav"))
    assert af.path == Path("test.wav")
    assert af.original_path is None
    assert af.preprocessed is False
    assert af.preprocessed_path is None
    assert af.temp_path is None


def test_audio_file_original_path():
    orig = Path("original.wav")
    af = AudioFile(path=Path("test.wav"), original_path=orig)
    assert af.original_path == orig


def test_pipeline_config_defaults():
    cfg = PipelineConfig()
    assert cfg.output_format == "docx"
    assert cfg.multi_pass is False
    assert cfg.initial_prompt is None
    assert cfg.qwen_available is False


def test_transcription_result_auto_preview():
    af = AudioFile(path=Path("test.wav"))
    tr = TranscriptionResult(audio=af, text="короткий текст")
    assert tr.preview == "короткий текст"
    assert tr.duration_sec == 0.0
    assert tr.output_path is None


def test_transcription_result_preview_truncated():
    af = AudioFile(path=Path("test.wav"))
    long_text = "слово " * 50
    tr = TranscriptionResult(audio=af, text=long_text)
    assert len(tr.preview) == 100 + 3  # 100 chars + "..."
    assert tr.preview.endswith("...")


def test_transcription_result_explicit_preview():
    af = AudioFile(path=Path("test.wav"))
    tr = TranscriptionResult(audio=af, text="текст", preview="кастомный превью")
    assert tr.preview == "кастомный превью"
