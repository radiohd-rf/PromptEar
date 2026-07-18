"""Интеграционный тест: реальная транскрибация короткого аудио.

Требует:
- tests/data/test_10s.wav (10-секундный WAV-файл)
- Установленный whisper (openai-whisper)
- Опционально: Ollama с Qwen 2.5 3b

Пропускается, если файл или whisper не найдены.
"""

import pytest

from config import WHISPER_MODEL
from processing.enhancer import OllamaEnhancer
from processing.transcriber import Transcriber


@pytest.mark.smoke
def test_real_transcribe(sample_audio_path):
    if not sample_audio_path.exists():
        pytest.skip(f"test_10s.wav not found at {sample_audio_path}")

    import importlib.util

    if importlib.util.find_spec("whisper") is None:
        pytest.skip("whisper not installed")

    transcriber = Transcriber()
    transcriber.load_model(model_name=WHISPER_MODEL)

    text = transcriber.transcribe(sample_audio_path, language="ru")

    assert isinstance(text, str)
    assert len(text) > 0, "Transcription returned empty text"
    assert len(text.split()) > 1, "Transcription returned single word"


@pytest.mark.smoke
def test_real_transcribe_with_enhancement(sample_audio_path, tmp_path):
    if not sample_audio_path.exists():
        pytest.skip(f"test_10s.wav not found at {sample_audio_path}")

    import importlib.util

    if importlib.util.find_spec("whisper") is None:
        pytest.skip("whisper not installed")

    transcriber = Transcriber()
    transcriber.load_model(model_name=WHISPER_MODEL)
    text = transcriber.transcribe(sample_audio_path, language="ru")

    if not text:
        pytest.skip("Transcription was empty, skipping enhancement test")

    enhancer = OllamaEnhancer()
    ollama_ok, model_ok = enhancer.is_available()

    if not (ollama_ok and model_ok):
        pytest.skip("Ollama or Qwen model not available")

    enhanced = enhancer.enhance(text)
    assert isinstance(enhanced, str)
    assert len(enhanced) > 0, "Enhanced text is empty"


@pytest.mark.smoke
def test_real_transcribe_save_txt(sample_audio_path, tmp_path):
    if not sample_audio_path.exists():
        pytest.skip(f"test_10s.wav not found at {sample_audio_path}")

    import importlib.util

    if importlib.util.find_spec("whisper") is None:
        pytest.skip("whisper not installed")

    from utils.files import save_txt

    transcriber = Transcriber()
    transcriber.load_model(model_name=WHISPER_MODEL)
    text = transcriber.transcribe(sample_audio_path, language="ru")

    if not text:
        pytest.skip("Transcription was empty")

    out_path = tmp_path / "result.txt"
    save_txt(out_path, text)
    assert out_path.exists()
    saved = out_path.read_text(encoding="utf-8")
    assert len(saved) > 0
