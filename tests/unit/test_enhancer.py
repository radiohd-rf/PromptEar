"""Тесты улучшения текста через Ollama Qwen."""

from unittest.mock import MagicMock

import pytest
import requests

from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TEMPERATURE
from processing.enhancer import OllamaEnhancer

MOCK_PATH = "processing.enhancer.requests.Session.post"


@pytest.fixture
def enhancer():
    return OllamaEnhancer()


def make_mock_response(status=200, text="исправленный текст"):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = {"response": text}
    resp.raise_for_status = MagicMock()
    return resp


# ── 1-pass ─────────────────────────────────────────────────────────────────


def test_enhance_calls_ollama(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH, return_value=make_mock_response())
    result = enhancer.enhance("какой-то текст")
    assert result == "исправленный текст"
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert url == f"{OLLAMA_BASE_URL}/api/generate"
    payload = mock_post.call_args[1]["json"]
    assert payload["model"] == OLLAMA_MODEL
    assert payload["temperature"] == OLLAMA_TEMPERATURE
    assert "какой-то текст" in payload["prompt"]


def test_enhance_returns_string(enhancer, mocker):
    mocker.patch(
        MOCK_PATH, return_value=make_mock_response(text="хороший текст")
    )
    result = enhancer.enhance("плохой текст")
    assert isinstance(result, str)
    assert len(result) > 0


def test_enhance_error_raises(enhancer, mocker):
    mocker.patch(
        MOCK_PATH, side_effect=requests.ConnectionError("timeout")
    )
    with pytest.raises(requests.ConnectionError):
        enhancer.enhance("test")


def test_enhance_with_context(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH, return_value=make_mock_response())
    enhancer.enhance("текст", context="тема: история")
    prompt = mock_post.call_args[1]["json"]["prompt"]
    assert "тема: история" in prompt


# ── 3-pass ─────────────────────────────────────────────────────────────────


def test_multi_pass_three_calls(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH)
    mock_post.return_value = make_mock_response(text="результат")
    enhancer.enhance_multi_pass("тестовый текст для проверки")
    assert mock_post.call_count == 3


def test_multi_pass_fallback_pass1(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH)
    mock_post.side_effect = [
        requests.ConnectionError("fail"),  # pass1 fails → fallback to original
        make_mock_response(text="pass2 полноценный результат проверки"),  # >60% длины
        make_mock_response(text="pass3 полноценный результат проверки"),
    ]
    result = enhancer.enhance_multi_pass("оригинальный текст для проверки")
    assert "pass3" in result  # last successful pass wins


def test_multi_pass_fallback_all(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH)
    mock_post.side_effect = requests.ConnectionError("fail")
    result = enhancer.enhance_multi_pass("оригинальный текст")
    assert result == "оригинальный текст"


def test_multi_pass_fallback_empty_response(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH)
    mock_post.side_effect = [
        make_mock_response(text="ok"),  # pass1 — <5 → fallback
        make_mock_response(text=""),  # pass2 — empty → fallback
        make_mock_response(text="pass3 полноценный после сбоя"),  # pass3 — ок
    ]
    result = enhancer.enhance_multi_pass("исходный текст для проверки многострочного")
    assert "pass3" in result


def test_multi_pass_too_short(enhancer, mocker):
    mock_post = mocker.patch(MOCK_PATH)
    mock_post.side_effect = [
        make_mock_response(text="коротко"),  # ~15% длины → fallback
        make_mock_response(text="pass2 полноценный результат после прохода номер два"),
        make_mock_response(text="pass3 полноценный результат после прохода номер три"),
    ]
    result = enhancer.enhance_multi_pass(
        "оригинальный длинный текст для проверки защитного механизма"
    )
    assert "pass3" in result  # last successful pass wins


# ── Speaker protection ─────────────────────────────────────────────────────


def test_protect_speakers(enhancer):
    text = "— Как дела?\n— Нормально."
    result = enhancer._protect_speakers(text)
    assert "[СПИКЕР 1]:" in result
    assert "[СПИКЕР 2]:" in result
    assert "Как дела?" in result
    assert "Нормально" in result


def test_restore_speakers(enhancer):
    text = "[СПИКЕР 1]: Как дела?\n[СПИКЕР 2]: Нормально."
    result = enhancer._restore_speakers(text)
    assert "— Как дела?" in result
    assert "— Нормально." in result


def test_protect_restore_roundtrip(enhancer):
    original = "— Как дела?\n— Нормально.\nа дальше просто текст"
    protected = enhancer._protect_speakers(original)
    restored = enhancer._restore_speakers(protected)
    assert restored == original


def test_protect_no_speakers(enhancer):
    text = "просто текст без диалогов"
    assert enhancer._protect_speakers(text) == text


# ── Length check ───────────────────────────────────────────────────────────


def test_result_too_short(enhancer):
    assert enhancer._result_too_short("коротко", "оригинальный длинный текст") is True


def test_result_ok_length(enhancer):
    assert (
        enhancer._result_too_short(
            "нормальный результат исправления", "оригинальный текст для исправления"
        )
        is False
    )


def test_result_too_long(enhancer):
    assert enhancer._result_too_short("x" * 100, "x" * 50) is True


def test_result_empty(enhancer):
    assert enhancer._result_too_short("", "текст") is True


def test_result_none(enhancer):
    assert enhancer._result_too_short("", "текст") is True
