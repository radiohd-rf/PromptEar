"""Определение тихого аудио и предобработка."""

import subprocess
import tempfile
from pathlib import Path

from config import FFMPEG_TIMEOUT, PREPROCESS_SAMPLE_RATE, QUIET_THRESHOLD_DB
from utils.protocol import QueueMsg


def detect_quiet_audio(path: Path, queue=None) -> bool:
    """Определяет, является ли аудио тихим (шёпот).

    Анализирует mean_volume через ffmpeg volumedetect.
    Если громкость ниже QUIET_THRESHOLD_DB — возвращает True (нужен усиленный режим).
    """
    try:
        cmd = [
            "ffmpeg",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=FFMPEG_TIMEOUT
        )
        for line in result.stderr.splitlines():
            if "mean_volume" in line:
                val = line.split(":")[1].strip().replace(" dB", "")
                mean_db = float(val)
                if queue:
                    queue.put((QueueMsg.LOG, f"  Средняя громкость: {mean_db:.1f} dB"))
                return mean_db < QUIET_THRESHOLD_DB
    except Exception as exc:
        if queue:
            queue.put((QueueMsg.LOG, f"  Не удалось проанализировать громкость: {exc}"))
    return False


def preprocess_audio(src_path: Path) -> Path:
    """Компрессия динамического диапазона + конвертация в PREPROCESS_SAMPLE_RATE моно WAV."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_path),
        "-af",
        "compand=0|0:1|1:-90/-60|-60/-40|-40/-30|-20/-20:6:0:-90:0.2",
        "-ar",
        str(PREPROCESS_SAMPLE_RATE),
        "-ac",
        "1",
        str(tmp_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, encoding="utf-8")
    return tmp_path
