"""AudioDetector — класс для детекции тихого аудио и предобработки."""

import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

from config import (
    FFMPEG_TIMEOUT,
    PREPROCESS_HIGHPASS_FREQ,
    PREPROCESS_LOWPASS_FREQ,
    PREPROCESS_SAMPLE_RATE,
    QUIET_THRESHOLD_DB,
)
from core.events import LogEvent, PipelineEvent


class AudioDetector:
    """Определяет тихое аудио и выполняет предобработку через ffmpeg."""

    @staticmethod
    def is_quiet(path: Path, emit: Callable[[PipelineEvent], None] | None = None) -> bool:
        """True если средняя громкость ниже порога."""
        try:
            path = path.resolve()
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
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=FFMPEG_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stderr.splitlines():
                if "mean_volume" in line:
                    val = line.split(":")[1].strip().replace(" dB", "")
                    mean_db = float(val)
                    if emit:
                        emit(LogEvent(f"  Средняя громкость: {mean_db:.1f} dB"))
                    return mean_db < QUIET_THRESHOLD_DB
        except Exception as exc:
            if emit:
                emit(LogEvent(f"  Не удалось проанализировать громкость: {exc}"))
        return False

    @staticmethod
    def preprocess(src_path: Path, quiet: bool = False, cancel: Event | None = None) -> Path:
        """Highpass/lowpass/normalize + 16kHz mono WAV. Для quiet — компрессия."""
        src_path = src_path.resolve()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        filters = [
            f"highpass=f={PREPROCESS_HIGHPASS_FREQ}",
            f"lowpass=f={PREPROCESS_LOWPASS_FREQ}",
            "loudnorm=I=-16:LRA=11",
        ]
        if quiet:
            filters.append("compand=0|0:1|1:-90/-60|-60/-40|-40/-30|-20/-20:6:0:-90:0.2")

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src_path),
            "-af",
            ",".join(filters),
            "-ar",
            str(PREPROCESS_SAMPLE_RATE),
            "-ac",
            "1",
            str(tmp_path),
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise RuntimeError("Отменено пользователем")
            time.sleep(0.25)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
        return tmp_path
