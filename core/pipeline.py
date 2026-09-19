"""Пайплайн обработки аудио: detect → preprocess → transcribe → enhance → save.

Архитектура: PipelineStep(ABC) + AudioPipeline(оркестратор).
"""

import re
import threading
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import Event
from typing import Any

from core.detector import AudioDetector
from core.events import (
    CancelledEvent,
    DoneEvent,
    DraftEvent,
    EnhancingEvent,
    EnhancingStreamEvent,
    ErrorEvent,
    FileStatusEvent,
    LogEvent,
    PipelineEvent,
    ProgressEvent,
    ResultEvent,
    SkippedEvent,
    TranscribingEvent,
)
from core.models import (
    ENHANCE_ASK,
    ENHANCE_NONE,
    AudioFile,
    PipelineConfig,
    TranscriptionResult,
)
from utils.files import save_docx, save_text_output
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
            preproc_path = AudioDetector.preprocess(
                filepath, quiet=whisper_mode, cancel=cancel, temp_dir=config.temp_dir
            )
            result.audio.preprocessed = True
        except Exception as exc:
            emit(LogEvent(f"  Ошибка предобработки: {exc}, работаю с оригиналом"))
            preproc_path = None
        result.audio.preprocessed_path = preproc_path or filepath
        if preproc_path:
            # не теряем старый temp_path (извлечённый из видео WAV) — чистим сразу
            old_tmp = result.audio.temp_path
            if old_tmp and old_tmp != preproc_path and old_tmp.exists():
                old_tmp.unlink(missing_ok=True)
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
            "beam_size": 5,
            "vad_filter": True,
        }
        if config.initial_prompt:
            kwargs["initial_prompt"] = config.initial_prompt

        filename = result.audio.path.name
        emit(FileStatusEvent(filename=filename, status="transcribing"))

        def on_segment(accumulated: str) -> None:
            emit(DraftEvent(text=accumulated, final=False))

        text, segments, duration = self._transcriber.transcribe_with_segments(
            audio_path, cancel=cancel, on_segment=on_segment, **kwargs
        )
        result.text = text
        result.segments = segments
        result.duration_sec = duration
        emit(DraftEvent(text=text, final=True))
        emit(LogEvent(f"  Распознано ({len(text)} символов): {result.preview}"))
        return result


class EnhanceStep(PipelineStep):
    """Улучшение текста через LLM."""

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
        if not result.text or not config.llm_available:
            return result
        if config.enhance_mode not in (ENHANCE_NONE, "auto", "ask"):
            return result
        if config.enhance_mode in (ENHANCE_ASK, ENHANCE_NONE):
            return result
        if getattr(result.audio, "skipped", False):
            return result

        filename = result.audio.path.name
        emit(FileStatusEvent(filename=filename, status="enhancing"))
        emit(LogEvent("  Многопроходное улучшение (3 прохода)..."))
        try:

            def mp_progress(msg: str) -> None:
                emit(LogEvent(f"    {msg}"))
                m = re.match(r"проход (\d)/(\d)", msg, re.IGNORECASE)
                if m:
                    emit(EnhancingEvent(int(m.group(1)), int(m.group(2))))

            def mp_stream(text_so_far: str, pass_no: int = 0) -> None:
                emit(EnhancingStreamEvent(
                    filename=filename,
                    text=text_so_far,
                    active_pass=pass_no,
                    final=False,
                ))

            result.text = self._enhancer.enhance_multi_pass(
                result.text,
                config.initial_prompt or "",
                progress_callback=mp_progress,
                cancel=cancel,
                stream_callback=mp_stream,
            )
            emit(ResultEvent(text=result.text, filename=filename))
            emit(LogEvent("  Многопроходное улучшение завершено"))
        except Exception as exc:
            emit(LogEvent(f"  Ошибка многопроходного улучшения: {exc}"))
        return result


class SaveStep(PipelineStep):
    """Сохранение результата в файл."""

    def __init__(self, result_store: dict | None = None) -> None:
        self._result_store = result_store if result_store is not None else {}

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

        filepath = result.audio.original_path or result.audio.path
        out_path = filepath.with_suffix(f".{config.output_format}")
        if config.output_format == "docx":
            save_docx(out_path, result.text)
        else:
            save_text_output(
                out_path,
                config.output_format,
                result.text,
                segments=result.segments,
                duration=result.duration_sec,
            )
        result.output_path = out_path
        self._result_store[str(result.audio.path)] = result
        status = "skipped" if result.audio.skipped else "done"
        emit(FileStatusEvent(filename=result.audio.path.name, status=status))
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
        result_store: dict | None = None,
        skip_requested: Callable[[str], bool] | None = None,
    ) -> None:
        """Запускает пайплайн для списка файлов.

        skip_requested(filename) — возвращает True, если пользователь запросил
        пропуск этого файла (per-file skip, не отменяет всю задачу).
        """
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
                elif isinstance(step, SaveStep):
                    steps[i] = SaveStep(result_store)

            start_time = time.time()

            for i, af in enumerate(files, 1):
                if cancel.is_set():
                    emit(CancelledEvent("Остановлено пользователем"))
                    break

                filepath = af.path
                emit(LogEvent(f"  [{i}/{total}] {filepath.name}"))
                emit(TranscribingEvent(f"Транскрибация: {filepath.name}..."))

                if skip_requested is not None and skip_requested(filepath.name):
                    emit(LogEvent(f"  {filepath.name} — пропущен"))
                    emit(SkippedEvent(filepath.name, "Пропущен по запросу"))
                    emit(FileStatusEvent(filename=filepath.name, status="skipped"))
                    emit(ProgressEvent(i, total, filepath.name, "-"))
                    continue

                # per-file cancel: глобальный cancel или skip текущего файла
                file_cancel = threading.Event()

                def watcher(
                    global_cancel: Event = cancel,
                    fc: Event = file_cancel,
                    name: str = filepath.name,
                ) -> None:
                    while True:
                        if global_cancel.is_set():
                            fc.set()
                            break
                        if skip_requested is not None and skip_requested(name):
                            fc.set()
                            break
                        time.sleep(0.05)

                threading.Thread(target=watcher, daemon=True).start()

                result = TranscriptionResult(audio=af, text="")

                # Per-file skip: прерываем enhancement (долгая LLM-фаза),
                # но транскрибация уже частично дала текст, и SaveStep
                # сохраняет частичный результат (см. спека v0.15 §2).
                for step in steps:
                    if isinstance(step, EnhanceStep) and file_cancel.is_set():
                        continue
                    result = step.process(result, config, emit, file_cancel)
                    if skip_requested is not None and skip_requested(filepath.name):
                        af.skipped = True
                        result.audio.skipped = True

                if cancel.is_set():
                    emit(CancelledEvent("Остановлено пользователем"))
                    break
                if af.skipped:
                    emit(LogEvent(f"  {filepath.name} — пропущен (частичный результат)"))
                    emit(SkippedEvent(filepath.name, "Пропущен, сохранён черновик"))

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
    result_store: dict | None = None,
    skip_requested: Callable[[str], bool] | None = None,
) -> None:
    """Legacy-враппер для обратной совместимости."""
    pipeline = AudioPipeline()
    pipeline.run(
        files,
        config,
        emit,
        cancel,
        transcriber=transcriber,
        enhancer=enhancer,
        result_store=result_store,
        skip_requested=skip_requested,
    )
