"""AudioDetector — класс для детекции тихого аудио и предобработки."""

import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from threading import Event

from config import (
    FFMPEG_TIMEOUT,
    PREPROCESS_HIGHPASS_FREQ,
    PREPROCESS_LOWPASS_FREQ,
    PREPROCESS_SAMPLE_RATE,
    QUIET_THRESHOLD_DB,
    TEMP_DIR,
)
from core.events import LogEvent, PipelineEvent


class AudioDetector:
    """Определяет тихое аудио и выполняет предобработку через ffmpeg."""

    @staticmethod
    def is_quiet(
        path: Path,
        emit: Callable[[PipelineEvent], None] | None = None,
        cancel: Event | None = None,
    ) -> bool:
        """True если средняя громкость ниже порога.

        Скан выполняется через Popen с опросом флага cancel: при отмене
        ffmpeg убивается и метод сразу возвращает False (файл всё равно
        пропускается, значение флага уже не важно). stderr дренируется в
        фоновом потоке, иначе переполнение буфера повесит ffmpeg навсегда.
        """
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
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert proc.stderr is not None
            lines: list[str] = []

            def _drain() -> None:
                assert proc.stderr is not None
                for chunk in proc.stderr:
                    lines.append(chunk.decode("utf-8", errors="replace"))

            drain = threading.Thread(target=_drain, daemon=True)
            drain.start()
            start = time.monotonic()
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    return False
                if time.monotonic() - start > FFMPEG_TIMEOUT:
                    proc.kill()
                    proc.wait()
                    raise TimeoutError(f"ffmpeg volumedetect > {FFMPEG_TIMEOUT}с")
                time.sleep(0.1)
            drain.join(timeout=2)
            for line in lines:
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
    def preprocess(
        src_path: Path,
        quiet: bool = False,
        cancel: Event | None = None,
        temp_dir: Path | None = None,
    ) -> Path:
        """Highpass/lowpass/normalize + 16kHz mono WAV. Для quiet — компрессия.

        Результат пишется в `temp_dir` (или `<корень>/temp` по умолчанию).
        """
        src_path = src_path.resolve()
        if temp_dir is None:
            temp_dir = TEMP_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = temp_dir / f"{uuid.uuid4().hex}.wav"

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
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # ffmpeg пишет лог в stderr — без чтения буфер переполняется и ffmpeg
        # блокируется навсегда (deadlock). Дренируем stderr в фоновом потоке.
        stderr_lines: list[str] = []

        def _drain() -> None:
            assert proc.stderr is not None
            assert proc.stdout is not None
            for chunk in proc.stderr:
                stderr_lines.append(chunk.decode("utf-8", errors="replace"))

        threading.Thread(target=_drain, daemon=True).start()
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise RuntimeError("Отменено пользователем")
            time.sleep(0.25)
        assert proc.stdout is not None
        assert proc.stderr is not None
        proc.stdout.close()
        proc.stderr.close()
        if proc.returncode != 0:
            err = "".join(stderr_lines)
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, b"", err.encode("utf-8", errors="replace")
            )
        return tmp_path
