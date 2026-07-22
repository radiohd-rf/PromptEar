"""Извлечение аудиодорожки из видео через ffmpeg."""

import subprocess
import tempfile
from pathlib import Path

from config import PREPROCESS_SAMPLE_RATE


def get_ffmpeg_path() -> str:
    _base = Path(__file__).resolve().parent.parent
    bundled = _base / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"


def extract_audio(video_path: Path, ffmpeg_path: str | None = None) -> Path:
    """Извлекает аудио из видео во временный WAV (16kHz, mono, PCM)."""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()

    video_path = video_path.resolve()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

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
