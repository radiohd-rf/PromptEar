"""Утилиты для работы с аудиофайлами."""

from pathlib import Path

from config import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from core.models import Segment

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


def save_md(path: Path, text: str):
    """Сохраняет текст в Markdown."""
    path.write_text(text, encoding="utf-8")


def save_docx(path: Path, text: str):
    """Сохраняет текст в DOCX."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    doc.save(str(path))


def _format_ts(seconds: float, separator: str = ",") -> str:
    """Форматирует секунды в SRT/VTT таймкод: HH:MM:SS,mmm (разделитель можно менять)."""
    millis = int(round(seconds * 1000))
    h, rem = divmod(millis, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"


def _subtitle_blocks(
    segments: list[Segment], text: str, duration: float
) -> list[tuple[float, float, str]]:
    """Строит блоки (start, end, text) для субтитров.

    Если сегментов нет — один блок на весь текст.
    """
    if segments:
        return [
            (seg.start, seg.end, seg.text.strip() or "…")
            for seg in segments
            if seg.text and seg.text.strip()
        ]
    if not text:
        return []
    return [(0.0, max(duration, 1.0), text)]


def save_srt(path: Path, segments: list[Segment], text: str = "", duration: float = 0.0):
    """Сохраняет субтитры в SRT."""
    blocks = _subtitle_blocks(segments, text, duration)
    lines: list[str] = []
    for i, (start, end, block_text) in enumerate(blocks, 1):
        lines.append(str(i))
        lines.append(f"{_format_ts(start)} --> {_format_ts(end)}")
        lines.append(block_text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def save_vtt(path: Path, segments: list[Segment], text: str = "", duration: float = 0.0):
    """Сохраняет субтитры в WebVTT."""
    blocks = _subtitle_blocks(segments, text, duration)
    lines = ["WEBVTT", ""]
    for start, end, block_text in blocks:
        lines.append(f"{_format_ts(start, '.')} --> {_format_ts(end, '.')}")
        lines.append(block_text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def save_text_output(
    path: Path,
    output_format: str,
    text: str,
    segments: list[Segment] | None = None,
    duration: float = 0.0,
):
    """Диспетчер сохранения результата по формату."""
    fmt = output_format.lower()
    segs = segments or []
    if fmt == "srt":
        save_srt(path, segs, text=text, duration=duration)
    elif fmt == "vtt":
        save_vtt(path, segs, text=text, duration=duration)
    elif fmt == "md":
        save_md(path, text)
    else:
        save_txt(path, text)
