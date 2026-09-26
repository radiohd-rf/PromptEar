"""Утилиты для работы с аудиофайлами."""

import re
from pathlib import Path

from config import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from core.models import Segment

ALL_SUPPORTED = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Таймкод-метка вида [MM:SS] или [HH:MM:SS], которую ставит Whisper/Gemma.
TS_MARKER_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")

# Метка «посреди предложения»: перед ней строчная буква, цифра, запятая или
# двоеточие без знака конца предложения (и не начало строки) — такую метку
# модель уронила в середину фразы, нужно перестроить разметку.
BAD_MARKER_RE = re.compile(r"[а-яёa-z0-9,;:]\s*\[\d{1,2}:\d{2}(?::\d{2})?\]")


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


def _format_ts_short(seconds: float) -> str:
    """Форматирует секунды в короткий таймкод: MM:SS (или HH:MM:SS для длинных файлов)."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def has_ts_markers(text: str) -> bool:
    """Проверяет, есть ли в тексте таймкод-метки [MM:SS]."""
    return bool(TS_MARKER_RE.search(text or ""))


def strip_ts_markers(text: str) -> str:
    """Удаляет таймкод-метки [MM:SS] из текста."""
    return TS_MARKER_RE.sub("", text or "")


# Параметры разбивки Whisper-сегментов на абзацы: пауза дольше ~1.5с или
# блок от 45 слов — граница нового абзаца (метка ставится только на его начало).
TS_PARAGRAPH_PAUSE_SEC = 1.5
TS_PARAGRAPH_MAX_WORDS = 45
# «Амортизатор»: если Whisper не поставил ни одной границы предложения подряд
# (3x нормы), абзац всё равно ломаем — иначе весь файл склеится в простыню.
TS_PARAGRAPH_HARD_WORDS = TS_PARAGRAPH_MAX_WORDS * 3

# Признак конца предложения (независимо от паузы): сегмент завершается точкой,
# вопросительным/восклицательным знаком, многоточием или закрывающей кавычкой/скобкой.
_SENTENCE_END_RE = re.compile(r"[.!?…][»\"')]*$")


def _ends_sentence(text: str) -> bool:
    """True, если сегмент заканчивается на границе предложения."""
    t = (text or "").strip()
    return bool(t) and bool(_SENTENCE_END_RE.search(t))


def _starts_sentence(text: str) -> bool:
    """True, если сегмент начинается заглавной/цифрой/тире (новое предложение).

    Whisper почти всегда пишет заглавную в начале новой фразы — это сигнал
    границы, даже если предыдущий сегмент не закончился точкой.
    """
    t = (text or "").strip()
    if not t:
        return False
    return t[0].isupper() or t[0].isdigit() or t[0] in '—–-«"»('


def has_bad_ts_markers(text: str) -> bool:
    """True, если в тексте есть метка [MM:SS] посреди предложения.

    Такую метку модель уронила в середину фразы (перед ней строчная буква,
    запятая, цифра и т.п.) — разметка сломана и её нужно перестроить.
    """
    return bool(BAD_MARKER_RE.search(text or ""))


def group_segments_into_paragraphs(
    segments: list[Segment],
    pause_sec: float = TS_PARAGRAPH_PAUSE_SEC,
    max_words: int = TS_PARAGRAPH_MAX_WORDS,
) -> list[list[Segment]]:
    """Группирует сегменты Whisper в абзацы.

    Метку ставят только на начало предложений — поэтому новый абзац начинается,
    ТОЛЬКО если предыдущий сегмент закончился на границе предложения (точка,
    вопрос, восклицание), И при этом была пауза (или блок набрал слов).
    Если пунктуация отсутствует, но идёт очень длинная пауза (>= 2x) и новый
    сегмент начинается с заглавной — это тоже граница предложения.
    Если Whisper обронил пунктуацию — запасной принудительный разрыв
    (hard cap), чтобы абзацы не превращались в простыню.
    """
    blocks: list[list[Segment]] = []
    current: list[Segment] = []
    words = 0
    for seg in segments:
        if not seg.text or not seg.text.strip():
            continue
        if current:
            prev = current[-1]
            pause = seg.start - prev.end
            prev_ends = _ends_sentence(prev.text)
            hard_cap = words >= TS_PARAGRAPH_HARD_WORDS
            if (
                (prev_ends and (pause > pause_sec or words >= max_words))
                or hard_cap
                or (not prev_ends and pause >= pause_sec * 2 and _starts_sentence(seg.text))
            ):
                blocks.append(current)
                current = []
                words = 0
        current.append(seg)
        words += len(seg.text.split())
    if current:
        blocks.append(current)
    return blocks


def build_paragraph_timestamps(segments: list[Segment], text: str) -> str:
    """Строит стенограмму с меткой [MM:SS] на начале каждого абзаца.

    Сегменты группируются в абзацы (по паузам и размеру), метка ставится на
    стартовый таймкод блока, абзацы разделяются пустой строкой.
    """
    if not segments:
        return text
    blocks = group_segments_into_paragraphs(segments)
    paras = []
    for block in blocks:
        block_text = " ".join(
            (s.text or "").strip() for s in block if s.text and s.text.strip()
        ).strip()
        if not block_text:
            continue
        paras.append(f"[{_format_ts_short(block[0].start)}] {block_text}")
    if not paras:
        return text
    return "\n\n".join(paras)


def ensure_timestamps(segments: list[Segment], text: str) -> str:
    """Гарантирует, что текст получит таймкод-метки [MM:SS] на абзацах.

    - Если метки уже стоят (расставила Gemma) И все на границах предложений —
      текст как есть.
    - Если текст совпадает со «склейкой» сегментов — точная разметка по абзацам.
    - Иначе (текст переработан ИИ / метки битые) — приблизительная привязка
      пропорционально, метки только в началах предложений.
    """
    if not segments or not text.strip():
        return text
    blocks = group_segments_into_paragraphs(segments)
    expected = len(blocks)
    if expected == 0:
        return text
    if len(TS_MARKER_RE.findall(text)) >= expected and not has_bad_ts_markers(text):
        return text

    clean = strip_ts_markers(text).strip()
    clean = " ".join(clean.split())
    raw = " ".join((s.text or "").strip() for s in segments if s.text and s.text.strip())
    if clean == raw:
        return build_paragraph_timestamps(segments, clean)
    return attach_timestamps_proportional(segments, clean)


def build_timestamps_text(segments: list[Segment], text: str) -> str:
    """Строит текст стенограммы с префиксами [MM:SS] перед каждым сегментом.

    Если сегментов нет — текст как есть. Пустые сегменты пропускаются.
    Устаревшая «init… маркировка каждой строки»; для файлов используйте
    build_paragraph_timestamps() — метка на начало абзаца.
    """
    if not segments:
        return text
    lines = []
    for seg in segments:
        seg_text = (seg.text or "").strip()
        if seg_text:
            lines.append(f"[{_format_ts_short(seg.start)}] {seg_text}")
    if not lines:
        return text
    return "\n".join(lines)


def attach_timestamps_proportional(segments: list[Segment], text: str) -> str:
    """Привязывает [MM:SS] к изменённому тексту приблизительно.

    Используется когда текст переписан ИИ-улучшением и точное соответствие
    сегментам потеряно: улучшенный текст режется по предложениям и
    распределяется по абзацам пропорционально длине исходных блоков,
    каждый абзац получает стартовый таймкод своего блока.
    """
    import re

    if not segments or not text:
        return text
    blocks = group_segments_into_paragraphs(segments)
    paras = []
    for block in blocks:
        block_text = " ".join(
            (s.text or "").strip() for s in block if s.text and s.text.strip()
        ).strip()
        if block_text:
            paras.append((block[0], block_text))
    if not paras:
        return text

    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s]
    if not sentences:
        return text

    weights = [len(t) for _s, t in paras]
    total_w = sum(weights)
    total_len = sum(len(s) for s in sentences)

    result = []
    idx = 0
    for (seg, _seg_txt), w in zip(paras, weights, strict=True):
        target = round(total_len * w / total_w) if total_w else 0
        group: list[str] = []
        group_len = 0
        # минимум одно предложение в абзаце — без пустых меток
        while idx < len(sentences) and (not group or group_len < target):
            sentence = sentences[idx]
            group.append(sentence)
            group_len += len(sentence)
            idx += 1
        if group:
            result.append(f"[{_format_ts_short(seg.start)}] {' '.join(group)}")

    rest = sentences[idx:]
    if rest and result:
        result[-1] = f"{result[-1]} {' '.join(rest)}"
    return "\n\n".join(result)


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


def translated_subtitles(segments: list[Segment], text: str, output_format: str) -> str:
    """Строит SRT/VTT из переведённого текста.

    Перевод не сохраняет точного соответствия сегментам, поэтому текст режется
    на предложения и распределяется по исходным сегментам пропорционально длине
    (как attach_timestamps_proportional) — каждый сегмент получает свой таймкод.
    """
    import re

    output_format = output_format.lower()
    if output_format not in ("srt", "vtt"):
        return text
    if not segments or not text.strip():
        return text

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if not sentences:
        return text

    # распределяем предложения по сегментам пропорционально длине сегментов
    weights = [len(s.text or "") for s in segments if s.text and s.text.strip()]
    segs = [s for s in segments if s.text and s.text.strip()]
    if not segs or sum(weights) == 0:
        return text
    total_w = sum(weights)
    total_len = sum(len(s) for s in sentences)

    blocks: list[tuple[float, float, str]] = []
    idx = 0
    for seg, w in zip(segs, weights, strict=True):
        target = round(total_len * w / total_w) if total_w else 0
        group: list[str] = []
        group_len = 0
        while idx < len(sentences) and (not group or group_len < target):
            sentence = sentences[idx]
            group.append(sentence)
            group_len += len(sentence)
            idx += 1
        if group:
            joined = " ".join(group).strip()
            if joined:
                blocks.append((seg.start, seg.end, joined))

    rest = sentences[idx:]
    if rest and blocks:
        s, e, t = blocks[-1]
        blocks[-1] = (s, e, f"{t} {' '.join(rest)}")

    if not blocks:
        return text

    lines: list[str] = []
    if output_format == "vtt":
        lines = ["WEBVTT", ""]
    for i, (start, end, block_text) in enumerate(blocks, 1):
        if output_format == "srt":
            lines.append(str(i))
            lines.append(f"{_format_ts(start)} --> {_format_ts(end)}")
        else:
            lines.append(f"{_format_ts(start, '.')} --> {_format_ts(end, '.')}")
        lines.append(block_text)
        lines.append("")
    return "\n".join(lines)
