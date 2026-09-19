"""Извлечение аудиодорожки из видео через ffmpeg."""

import subprocess
import uuid
from pathlib import Path

from config import PREPROCESS_SAMPLE_RATE, TEMP_DIR


def get_ffmpeg_path() -> str:
    _base = Path(__file__).resolve().parent.parent
    bundled = _base / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"


def extract_audio(
    video_path: Path,
    ffmpeg_path: str | None = None,
    temp_dir: Path | None = None,
) -> Path:
    """Извлекает аудио из видео во временный WAV (16kHz, mono, PCM).

    Файл создаётся в `temp_dir` (или `<корень>/temp` по умолчанию).
    """
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    if temp_dir is None:
        temp_dir = TEMP_DIR

    temp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = temp_dir / f"{uuid.uuid4().hex}.wav"

    video_path = video_path.resolve()
    cmd = [
        ffmpeg_path,
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(PREPROCESS_SAMPLE_RATE),
        "-ac", "1",
        "-y",
        str(wav_path),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        if wav_path.exists():
            wav_path.unlink(missing_ok=True)
        stderr = result.stderr or ""
        if "Invalid data found when processing" in stderr:
            raise ValueError(f"Не удалось прочитать видео: {video_path.name}")
        if "No audio stream" in stderr or "could not find codec" in stderr:
            raise ValueError(f"В видео {video_path.name} нет аудиодорожки")
        raise RuntimeError(f"Ошибка ffmpeg: {stderr[:200]}")

    return wav_path
