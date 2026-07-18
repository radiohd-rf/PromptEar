"""Глобальные фикстуры для тестов PromptEar."""

from pathlib import Path

import pytest

from utils.gpu import clear_gpu_cache


@pytest.fixture(autouse=True)
def _clear_gpu_cache():
    clear_gpu_cache()


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Короткий алиас для tmp_path."""
    return tmp_path


@pytest.fixture
def sample_text() -> str:
    return "Это образцовый текст для транскрибации. Он содержит несколько предложений."


@pytest.fixture
def sample_audio_path() -> Path:
    return Path(__file__).parent / "data" / "test_10s.wav"
