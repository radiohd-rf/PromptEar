"""Пайплайн обработки аудио: detect → preprocess → transcribe → enhance → save.

Архитектура: PipelineStep(ABC) + AudioPipeline(оркестратор).
"""

import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from config import (
    WHISPER_BEST_OF,
    WHISPER_COMPRESSION_RATIO_THRESHOLD,
    WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
    WHISPER_LOGPROB_THRESHOLD,
    WHISPER_NO_SPEECH_THRESHOLD,
    WHISPER_TEMPERATURE,
)
from core.events import (
    CancelledEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    PipelineEvent,
    ProgressEvent,
    SetBusyEvent,
    TranscribingEvent,
)
from core.models import AudioFile, PipelineConfig, TranscriptionResult
from core.detector import AudioDetector
from utils.files import save_docx, save_txt
from utils.gpu import get_torch_device


class PipelineStep(ABC):
    """Базовый шаг пайплайна. Каждый шаг принимает TranscriptionResult и возвращает его."""

    @abstractmethod
    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class DetectPreprocessStep(PipelineStep):
    """Детекция тихого аудио + предобработка ffmpeg."""

    @property
    def name(self) -> str:
        return "detect+preprocess"

    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        filepath = result.audio.path
        whisper_mode = AudioDetector.is_quiet(filepath)
        if whisper_mode:
            emit(LogEvent("  Тихий звук — включаю усиленный режим"))

        emit(LogEvent("  Предобработка (фильтрация + нормализация)..."))
        try:
            preproc_path = AudioDetector.preprocess(filepath, quiet=whisper_mode)
            result.audio.preprocessed = True
        except Exception as exc:
            emit(LogEvent(f"  Ошибка предобработки: {exc}, работаю с оригиналом"))
            preproc_path = None
        result.audio.preprocessed_path = preproc_path or filepath
        result.audio.temp_path = preproc_path
        return result


class TranscribeStep(PipelineStep):
    """Транскрибация через Whisper."""

    def __init__(self, transcriber: Any) -> None:
        self._transcriber = transcriber

    @property
    def name(self) -> str:
        return "transcribe"

    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        audio_path = result.audio.preprocessed_path
        kwargs: dict[str, Any] = {
            "language": "ru",
            "task": "transcribe",
            "verbose": True,
            "temperature": WHISPER_TEMPERATURE,
            "compression_ratio_threshold": WHISPER_COMPRESSION_RATIO_THRESHOLD,
            "logprob_threshold": WHISPER_LOGPROB_THRESHOLD,
            "no_speech_threshold": WHISPER_NO_SPEECH_THRESHOLD,
            "hallucination_silence_threshold": WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
            "best_of": WHISPER_BEST_OF,
        }
        if config.initial_prompt:
            kwargs["initial_prompt"] = config.initial_prompt

        text = self._transcriber.transcribe(audio_path, queue=None, **kwargs)
        result.text = text
        emit(LogEvent(f"  Распознано ({len(text)} символов): {result.preview}"))
        return result


class EnhanceStep(PipelineStep):
    """Улучшение текста через Qwen."""

    def __init__(self, enhancer: Any) -> None:
        self._enhancer = enhancer

    @property
    def name(self) -> str:
        return "enhance"

    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        if not result.text or not config.qwen_available:
            return result

        emit(LogEvent("  Многопроходное улучшение Qwen (3 прохода)..."))
        try:

            def mp_progress(msg: str) -> None:
                emit(LogEvent(f"    {msg}"))

            result.text = self._enhancer.enhance_multi_pass(
                result.text, config.initial_prompt or "", progress_callback=mp_progress
            )
            emit(LogEvent("  Многопроходное улучшение завершено"))
        except Exception as exc:
            emit(LogEvent(f"  Ошибка многопроходного улучшения: {exc}"))
        return result


class SaveStep(PipelineStep):
    """Сохранение результата в файл."""

    @property
    def name(self) -> str:
        return "save"

    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        if not result.text:
            emit(LogEvent(f"{result.audio.path.name} — пустой результат"))
            return result

        filepath = result.audio.path
        out_path = filepath.with_suffix(f".{config.output_format}")
        if config.output_format == "txt":
            save_txt(out_path, result.text)
        elif config.output_format == "docx":
            save_docx(out_path, result.text)
        result.output_path = out_path
        emit(LogEvent(f"{filepath.name} -> {out_path.name}"))
        return result


class CleanupStep(PipelineStep):
    """Удаление временных файлов."""

    @property
    def name(self) -> str:
        return "cleanup"

    def process(
        self,
        result: TranscriptionResult,
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
    ) -> TranscriptionResult:
        tmp = result.audio.temp_path
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)
        return result


class AudioPipeline:
    """Оркестратор пайплайна: итерирует файлы, прогоняет через список шагов."""

    def __init__(self, steps: list[PipelineStep] | None = None) -> None:
        self.steps = steps or [
            DetectPreprocessStep(),
            TranscribeStep(None),   # будет заменён в run()
            EnhanceStep(None),      # будет заменён в run()
            SaveStep(),
            CleanupStep(),
        ]

    def run(
        self,
        files: list[AudioFile],
        config: PipelineConfig,
        emit: Callable[[PipelineEvent], None],
        cancel: Event,
        transcriber: Any = None,
        enhancer: Any = None,
    ) -> None:
        """Запускает пайплайн для списка файлов."""
        try:
            total = len(files)
            device = get_torch_device().upper()
            emit(LogEvent(f"  Модель: medium | Устройство: {device}"))

            steps = list(self.steps)
            for i, step in enumerate(steps):
                if isinstance(step, TranscribeStep):
                    steps[i] = TranscribeStep(transcriber)
                elif isinstance(step, EnhanceStep):
                    steps[i] = EnhanceStep(enhancer)

            start_time = time.time()

            for i, af in enumerate(files, 1):
                if cancel.is_set():
                    emit(CancelledEvent("Остановлено пользователем"))
                    break

                filepath = af.path
                emit(LogEvent(f"  [{i}/{total}] {filepath.name}"))
                emit(TranscribingEvent(f"Транскрибация: {filepath.name}..."))

                result = TranscriptionResult(audio=af, text="")

                for step in steps:
                    if cancel.is_set():
                        emit(CancelledEvent("Остановлено пользователем"))
                        break
                    result = step.process(result, config, emit, cancel)

                if cancel.is_set():
                    break

                file_end = time.time()
                elapsed = file_end - start_time
                avg_time = elapsed / i
                remaining = (total - i) * avg_time
                eta_min = int(remaining // 60)
                eta_sec = int(remaining % 60)
                eta_str = f"{eta_min}м {eta_sec}с" if eta_min > 0 else f"{eta_sec}с"
                emit(ProgressEvent(i, total, filepath.name, eta_str))

            if cancel.is_set():
                emit(CancelledEvent("Остановлено пользователем"))
            else:
                emit(DoneEvent(f"Готово. Обработано {len(files)} файлов."))

        except Exception as exc:
            tb = traceback.format_exc()
            emit(ErrorEvent(f"Ошибка: {exc}\n{tb}"))


def run_pipeline(
    files: list[AudioFile],
    config: PipelineConfig,
    emit: Callable[[PipelineEvent], None],
    cancel: Event,
    transcriber: Any,
    enhancer: Any,
) -> None:
    """Legacy-враппер для обратной совместимости."""
    pipeline = AudioPipeline()
    pipeline.run(files, config, emit, cancel, transcriber=transcriber, enhancer=enhancer)
