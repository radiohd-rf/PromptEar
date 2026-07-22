"""Утилиты для работы с аудиофайлами."""

from pathlib import Path

from config import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS


ALL_SUPPORTED = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def find_supported_files(paths: list) -> list[Path]:
    """Собирает все аудио и видеофайлы из переданных путей."""
    files = []
    for path in paths:
        p = Path(path)
        if p.is_file() and p.suffix.lower() in ALL_SUPPORTED:
            files.append(p)
        elif p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in ALL_SUPPORTED:
                    files.append(child)
    return files


def find_audio_files(paths: list) -> list[Path]:
    """Собирает все аудиофайлы из переданных путей (файлы или папки)."""
    files = []
    for path in paths:
        p = Path(path)
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(p)
        elif p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
                    files.append(child)
    return files


def save_txt(path: Path, text: str):
    """Сохраняет текст в TXT."""
    path.write_text(text, encoding="utf-8")


def save_docx(path: Path, text: str):
    """Сохраняет текст в DOCX."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    doc.save(str(path))
