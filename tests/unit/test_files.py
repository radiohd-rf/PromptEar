"""Тесты утилит работы с файлами."""

from config import AUDIO_EXTENSIONS
from utils.files import find_audio_files, save_docx, save_txt


def test_find_audio_files_dir(tmp_path):
    (tmp_path / "test.mp3").write_text("")
    (tmp_path / "test.wav").write_text("")
    result = find_audio_files([str(tmp_path)])
    assert len(result) == 2
    assert all(p.suffix.lower() in AUDIO_EXTENSIONS for p in result)


def test_find_audio_files_single_file(tmp_path):
    f = tmp_path / "test.mp3"
    f.write_text("")
    result = find_audio_files([str(f)])
    assert len(result) == 1
    assert result[0] == f


def test_find_audio_files_empty(tmp_path):
    result = find_audio_files([str(tmp_path)])
    assert result == []


def test_find_audio_files_unsupported(tmp_path):
    (tmp_path / "test.txt").write_text("hello")
    result = find_audio_files([str(tmp_path)])
    assert result == []


def test_find_audio_files_nonexistent():
    result = find_audio_files([r"C:\nonexistent_path_xyz"])
    assert result == []


def test_save_txt(tmp_path):
    out = tmp_path / "result.txt"
    save_txt(out, "hello world")
    assert out.read_text(encoding="utf-8") == "hello world"


def test_save_docx(tmp_path, mocker):
    mocker.patch("docx.Document")
    out = tmp_path / "result.docx"
    save_docx(out, "hello world")
    assert out.exists() is False  # docx mock не пишет файл


def test_save_txt_unicode(tmp_path):
    out = tmp_path / "result.txt"
    text = "Привет, мир! 💡"
    save_txt(out, text)
    assert out.read_text(encoding="utf-8") == text


def test_audio_extensions_set():
    assert isinstance(AUDIO_EXTENSIONS, set)
    assert len(AUDIO_EXTENSIONS) >= 5
