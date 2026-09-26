"""Тесты утилит работы с файлами/таймкодами (utils/files.py)."""

from pathlib import Path

from core.models import Segment
from utils import files


def _seg(start: float, end: float, text: str) -> Segment:
    return Segment(start=start, end=end, text=text)


def test_is_video_file() -> None:
    assert files.is_video_file(Path("clip.mp4")) is True
    assert files.is_video_file(Path("clip.MP4")) is True
    assert files.is_video_file(Path("audio.mp3")) is False


def test_all_supported_contains_audio_and_video() -> None:
    assert ".mp3" in files.ALL_SUPPORTED
    assert ".mp4" in files.ALL_SUPPORTED
    assert ".bmp" not in files.ALL_SUPPORTED


def test_find_supported_files_skips_unsupported(tmp_path) -> None:
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    found = files.find_supported_files([tmp_path])
    assert found == [tmp_path / "a.mp3"]


def test_find_audio_files_excludes_video(tmp_path) -> None:
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "v.mp4").write_bytes(b"x")
    found = files.find_audio_files([tmp_path])
    assert found == [tmp_path / "a.mp3"]


def test_format_ts_short() -> None:
    assert files._format_ts_short(0) == "00:00"
    assert files._format_ts_short(5) == "00:05"
    assert files._format_ts_short(65) == "01:05"
    assert files._format_ts_short(3725) == "01:02:05"


def test_format_ts() -> None:
    assert files._format_ts(0) == "00:00:00,000"
    assert files._format_ts(61.5) == "00:01:01,500"
    assert files._format_ts(3661.25, ".") == "01:01:01.250"


def test_has_and_strip_ts_markers() -> None:
    text = "Привет [01:23] мир"
    assert files.has_ts_markers(text) is True
    assert files.has_ts_markers("Без меток") is False
    assert files.strip_ts_markers(text) == "Привет  мир"
    assert files.has_ts_markers(files.strip_ts_markers(text)) is False


def test_has_bad_ts_markers() -> None:
    assert files.has_bad_ts_markers("слово [01:23] дальше") is True
    assert files.has_bad_ts_markers("Слово. [01:23] Дальше") is False
    assert files.has_bad_ts_markers("[01:23] Начало строки") is False


def test_group_segments_into_paragraphs_by_pause() -> None:
    segments = [
        _seg(0.0, 3.0, "Первое предложение."),
        _seg(5.0, 8.0, "Второе после паузы."),
    ]
    blocks = files.group_segments_into_paragraphs(segments)
    assert len(blocks) == 2
    assert [s.text for s in blocks[0]] == ["Первое предложение."]


def test_group_segments_no_split_on_short_pause() -> None:
    segments = [
        _seg(0.0, 3.0, "Первое предложение."),
        _seg(3.1, 6.0, "Второе без паузы."),
    ]
    blocks = files.group_segments_into_paragraphs(segments)
    assert len(blocks) == 1
    assert len(blocks[0]) == 2


def test_group_segments_hard_cap_splits_long_blocks() -> None:
    words = " ".join(["слово"] * 200)
    segments = [_seg(0.0, 10.0, f"{words} конец."), _seg(10.1, 12.0, "Хвост.")]
    blocks = files.group_segments_into_paragraphs(segments)
    assert len(blocks) == 2
    assert len(blocks[0]) == 1
    assert len(blocks[1]) == 1


def test_build_paragraph_timestamps() -> None:
    segments = [
        _seg(0.0, 3.0, "Ноль."),
        _seg(5.0, 8.0, "Пять."),
    ]
    text = "Ноль. Пять."
    result = files.build_paragraph_timestamps(segments, text)
    assert result == "[00:00] Ноль.\n\n[00:05] Пять."


def test_build_paragraph_timestamps_empty_segments() -> None:
    assert files.build_paragraph_timestamps([], "текст") == "текст"


def test_ensure_timestamps_keeps_good_markers() -> None:
    segments = [_seg(0.0, 3.0, "Ноль."), _seg(5.0, 8.0, "Пять.")]
    good = "[00:00] Ноль.\n\n[00:05] Пять."
    assert files.ensure_timestamps(segments, good) == good


def test_ensure_timestamps_rebuilds_bad_markers() -> None:
    segments = [_seg(0.0, 3.0, "Ноль."), _seg(5.0, 8.0, "Пять.")]
    bad = "текст [00:00] посреди фразы"
    result = files.ensure_timestamps(segments, bad)
    assert files.has_bad_ts_markers(result) is False
    assert files.has_ts_markers(result) is True


def test_ensure_timestamps_exact_concat() -> None:
    segments = [_seg(0.0, 3.0, "Ноль."), _seg(5.0, 8.0, "Пять.")]
    raw = " ".join(s.text for s in segments)
    result = files.ensure_timestamps(segments, raw)
    assert result.startswith("[00:00]")


def test_ensure_timestamps_empty_returns_text() -> None:
    assert files.ensure_timestamps([], "  ") == "  "
    assert files.ensure_timestamps([], "") == ""


def test_build_timestamps_text() -> None:
    segments = [_seg(0.0, 3.0, "Ноль."), _seg(5.0, 8.0, "")]
    assert files.build_timestamps_text(segments, "") == "[00:00] Ноль."
    assert files.build_timestamps_text([], "текст") == "текст"


def test_attach_timestamps_proportional() -> None:
    segments = [_seg(0.0, 3.0, "Первый блок."), _seg(5.0, 8.0, "Второй блок.")]
    rewritten = "Первое улучшенное предложение. Второе улучшенное предложение."
    result = files.attach_timestamps_proportional(segments, rewritten)
    assert result.count("[0") >= 1
    assert " ".join(files.strip_ts_markers(result).split()) == rewritten


def test_attach_timestamps_proportional_no_segments() -> None:
    assert files.attach_timestamps_proportional([], "текст") == "текст"


def test_save_srt(tmp_path) -> None:
    segments = [_seg(0.0, 1.5, "Привет."), _seg(2.0, 3.0, "Мир.")]
    out = tmp_path / "out.srt"
    files.save_srt(out, segments)
    content = out.read_text(encoding="utf-8")
    assert "1" in content
    assert "00:00:00,000 --> 00:00:01,500" in content
    assert "Привет." in content
    assert "Мир." in content


def test_save_vtt(tmp_path) -> None:
    segments = [_seg(0.0, 1.5, "Привет.")]
    out = tmp_path / "out.vtt"
    files.save_vtt(out, segments)
    content = out.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in content


def test_save_text_output_txt_and_md(tmp_path) -> None:
    txt = tmp_path / "out.txt"
    md = tmp_path / "out.md"
    files.save_text_output(txt, "txt", "текст")
    files.save_text_output(md, "md", "текст")
    assert txt.read_text(encoding="utf-8") == "текст"
    assert md.read_text(encoding="utf-8") == "текст"


def test_save_text_output_defaults_to_txt(tmp_path) -> None:
    out = tmp_path / "out.unknown"
    files.save_text_output(out, "unknown", "текст")
    assert out.read_text(encoding="utf-8") == "текст"


def test_translated_subtitles_srt() -> None:
    segments = [_seg(0.0, 2.0, "Hello."), _seg(3.0, 5.0, "World.")]
    translated = "Привет мир. Мир приветствует."
    result = files.translated_subtitles(segments, translated, "srt")
    assert "00:00:00,000 --> 00:00:02,000" in result
    assert "Привет мир." in result
    assert "Мир приветствует." in result


def test_translated_subtitles_vtt() -> None:
    segments = [_seg(0.0, 2.0, "Hello.")]
    result = files.translated_subtitles(segments, "Привет.", "vtt")
    assert result.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.000" in result


def test_translated_subtitles_unsupported_format() -> None:
    assert files.translated_subtitles([], "текст", "docx") == "текст"
    assert files.translated_subtitles([], "текст", "txt") == "текст"


def test_ends_sentence() -> None:
    assert files._ends_sentence("Привет.") is True
    assert files._ends_sentence("Конец?") is True
    assert files._ends_sentence("Просто текст") is False
    assert files._ends_sentence("") is False


def test_starts_sentence() -> None:
    assert files._starts_sentence("Авто") is True
    assert files._starts_sentence("123") is True
    assert files._starts_sentence("— тире") is True
    assert files._starts_sentence("строй") is False
    assert files._starts_sentence("") is False
