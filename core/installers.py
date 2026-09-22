"""Установщики LLM-движков (llama.cpp / SAGE)."""

import contextlib
import threading
from collections.abc import Callable

from core.events import (
    LlmReadyEvent,
    LogEvent,
    PipelineEvent,
    SetBusyEvent,
)
from processing.enhancer import BaseEnhancer


class GemmaInstaller:
    """Скачивание GGUF-модели gemma-4-E2B и запуск llama-server."""

    @staticmethod
    def download_and_start(
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Скачивает GGUF по config.GGUF_URL в models/llm и поднимает llama-server.

        cancel — событие отмены: при установке скачивание прерывается,
        сервер не стартует, возвращается False.
        """
        from processing.enhancer import LlamaCppEnhancer

        enhancer = LlamaCppEnhancer()
        enhancer.download_model(on_progress=on_progress, cancel=cancel)
        if cancel is not None and cancel.is_set():
            return False
        # модель скачана; если сервер не поднялся — LLM-error-box подскажет
        with contextlib.suppress(Exception):
            enhancer.start_server()
        return True


class LlamaCppInstaller:
    """Установка llama.cpp (llama-server) и модели gemma-4-E2B."""

    @staticmethod
    def check(
        enhancer: BaseEnhancer,
        emit: Callable[[PipelineEvent], None],
        install_callback: Callable[[], None],
    ) -> None:
        """Асинхронная проверка LLM-движка — запускает установку при необходимости."""
        def check():
            ok, model = enhancer.is_available()
            if ok and model:
                emit(LlmReadyEvent(True, True))
            elif ok and not model:
                emit(LlmReadyEvent(True, False))
            else:
                emit(LlmReadyEvent(False, False))
        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def install(
        enhancer: BaseEnhancer,
        emit: Callable[[PipelineEvent], None],
    ) -> None:
        """Скачивает llama.cpp и модель в фоновом потоке."""
        emit(LogEvent("Скачивание llama.cpp..."))
        emit(SetBusyEvent(True))

        def install_worker():
            try:
                def on_progress(msg):
                    emit(LogEvent(msg))
                enhancer.install(progress_callback=on_progress)
                emit(LlmReadyEvent(True, True))
            except Exception as exc:
                emit(LogEvent(f"Ошибка установки llama.cpp: {exc}"))
                emit(LogEvent("  Попробуйте скачать вручную: https://github.com/ggml-org/llama.cpp/releases"))
                emit(LlmReadyEvent(False, False))
            finally:
                emit(SetBusyEvent(False))

        threading.Thread(target=install_worker, daemon=True).start()


class SageInstaller:
    """Установка SAGE-1.7B (FRED-T5-1.7B) — скачивание модели с HuggingFace."""

    @staticmethod
    def check(
        enhancer: BaseEnhancer,
        emit: Callable[[PipelineEvent], None],
        install_callback: Callable[[], None],
    ) -> None:
        def check():
            ok, model = enhancer.is_available()
            if ok and model:
                emit(LlmReadyEvent(True, True))
            elif ok and not model:
                emit(LlmReadyEvent(True, False))
            else:
                emit(LlmReadyEvent(False, False))
        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def install(
        enhancer: BaseEnhancer,
        emit: Callable[[PipelineEvent], None],
    ) -> None:
        emit(LogEvent("Скачивание SAGE-1.7B..."))
        emit(SetBusyEvent(True))

        def install_worker():
            try:
                def on_progress(msg):
                    emit(LogEvent(msg))
                enhancer.install(progress_callback=on_progress)
                emit(LlmReadyEvent(True, True))
            except Exception as exc:
                emit(LogEvent(f"Ошибка установки SAGE: {exc}"))
                emit(LlmReadyEvent(False, False))
            finally:
                emit(SetBusyEvent(False))

        threading.Thread(target=install_worker, daemon=True).start()
