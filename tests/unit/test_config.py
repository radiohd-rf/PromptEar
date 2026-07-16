"""Тесты конфигурации — типы, границы, валидность."""

import config


def test_colors_are_hex():
    for name, value in config.COLORS.items():
        assert isinstance(value, str), f"{name} not a string"
        assert value.startswith("#"), f"{name} missing # prefix"
        assert len(value) in (4, 7), f"{name} invalid hex length: {value}"


def test_ollama_url_valid():
    assert config.OLLAMA_BASE_URL.startswith("http://") or config.OLLAMA_BASE_URL.startswith(
        "https://"
    )


def test_temperature_range():
    assert 0.0 <= config.OLLAMA_TEMPERATURE <= 1.0


def test_ratios_valid():
    assert config.MULTI_PASS_MIN_RATIO < config.MULTI_PASS_MAX_RATIO


def test_audio_extensions_lowercase():
    for ext in config.AUDIO_EXTENSIONS:
        assert ext == ext.lower(), f"{ext} not lowercase"


def test_spinner_frames_not_empty():
    assert len(config.SPINNER_FRAMES) > 0


def test_window_size_positive():
    assert config.WINDOW_WIDTH > 0
    assert config.WINDOW_HEIGHT > 0


def test_output_formats_has_docx_and_txt():
    assert "docx" in config.OUTPUT_FORMATS
    assert "txt" in config.OUTPUT_FORMATS


def test_default_format_is_docx():
    assert config.DEFAULT_FORMAT == "docx"


def test_font_family():
    assert isinstance(config.FONT_FAMILY, str) and len(config.FONT_FAMILY) > 0


def test_font_sizes_positive():
    assert config.FONT_SIZE > 0
    assert config.FONT_SIZE_SMALL > 0


def test_quiet_threshold():
    assert isinstance(config.QUIET_THRESHOLD_DB, int | float)
    assert config.QUIET_THRESHOLD_DB < 0


def test_ffmpeg_timeout_positive():
    assert config.FFMPEG_TIMEOUT > 0


def test_ollama_timeout_positive():
    assert config.OLLAMA_TIMEOUT > 0


def test_nvidia_smi_timeout_positive():
    assert config.NVIDIA_SMI_TIMEOUT > 0
