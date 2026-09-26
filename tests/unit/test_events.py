"""Тесты типизированных событий (core/events.py)."""

from core.events import (
    DoneEvent,
    DraftEvent,
    EnhancingStreamEvent,
    ErrorEvent,
    FileStatusEvent,
    LogEvent,
    ProgressEvent,
    ResultEvent,
    SkippedEvent,
    TranscribingEvent,
)


def test_draft_event_defaults() -> None:
    ev = DraftEvent(text="привет")
    assert ev.text == "привет"
    assert ev.final is False
    assert ev.filename == ""


def test_draft_event_full() -> None:
    ev = DraftEvent(text="текст", final=True, filename="a.wav")
    assert (ev.text, ev.final, ev.filename) == ("текст", True, "a.wav")


def test_enhancing_stream_defaults() -> None:
    ev = EnhancingStreamEvent(filename="f.mp3", text="")
    assert ev.active_pass == 0
    assert ev.final is False


def test_file_status_default_audio_ok() -> None:
    ev = FileStatusEvent(filename="f.wav", status="done")
    assert ev.audio_ok is False


def test_simple_events() -> None:
    assert LogEvent("сообщение").message == "сообщение"
    assert ErrorEvent("ошибка").message == "ошибка"
    assert DoneEvent("готово").message == "готово"
    assert SkippedEvent("f").message == ""
    assert TranscribingEvent("работаем").message == "работаем"
    assert ResultEvent("текст", "f").filename == "f"


def test_progress_event() -> None:
    ev = ProgressEvent(current=1, total=3, filename="f", eta="10с")
    assert (ev.current, ev.total, ev.filename, ev.eta) == (1, 3, "f", "10с")
