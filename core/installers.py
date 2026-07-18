"""Установщики CUDA и Ollama (вынос из ui/root.py)."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable

import requests

from core.events import (
    CudaInstalledEvent,
    LogEvent,
    OllamaReadyEvent,
    PipelineEvent,
    SetBusyEvent,
)
from processing.enhancer import OllamaEnhancer


class CudaInstaller:
    """Установка CUDA-версии torch в виртуальном окружении."""

    @staticmethod
    def install(emit: Callable[[PipelineEvent], None]) -> None:
        """Запускает установку в фоновом потоке."""
        threading.Thread(target=CudaInstaller._install_thread, args=(emit,), daemon=True).start()

    @staticmethod
    def _install_thread(emit: Callable[[PipelineEvent], None]) -> None:
        python = sys.executable
        try:
            import torch

            if torch.cuda.is_available():
                emit(LogEvent("CUDA torch already installed, skipping"))
                return
        except Exception:
            pass

        emit(LogEvent("Installing CUDA torch in venv..."))
        emit(SetBusyEvent(True))

        try:
            emit(LogEvent("  Removing CPU torch..."))
            subprocess.run(
                [python, "-m", "pip", "uninstall", "-y", "torch", "torchaudio"],
                capture_output=True, timeout=120,
            )
            cuda_ok = False
            for cuda_ver in ("cu126",):
                for attempt in range(2):
                    if attempt > 0:
                        emit(LogEvent(f"  Retrying {cuda_ver}..."))
                    result = subprocess.run(
                        [python, "-m", "pip", "install", "--quiet",
                         "--timeout", "300", "--trusted-host",
                         "download.pytorch.org",
                         "--index-url", f"https://download.pytorch.org/whl/{cuda_ver}",
                         "torch==2.11.0+cu126", "torchaudio==2.11.0+cu126"],
                        capture_output=True, text=True, timeout=900,
                    )
                    if result.returncode == 0:
                        verify = subprocess.run(
                            [python, "-c",
                             "import torch; print(f'torch={torch.__version__}'"
                             " f' cuda={torch.version.cuda}'"
                             " f' available={torch.cuda.is_available()}')"],
                            capture_output=True, text=True, timeout=30,
                        )
                        if "available=True" in verify.stdout:
                            emit(LogEvent(f"  {cuda_ver}: CUDA OK"))
                            cuda_ok = True
                            break
                    else:
                        err = (result.stderr or "").strip()[:300]
                        emit(LogEvent(f"  {cuda_ver}: {err}" if err else f"  {cuda_ver}: failed"))
                if cuda_ok:
                    break

            if cuda_ok:
                emit(LogEvent("CUDA torch installed! Restarting..."))
                emit(CudaInstalledEvent(True))
            else:
                emit(LogEvent("CUDA install failed, falling back to CPU..."))
                subprocess.run(
                    [python, "-m", "pip", "install", "--quiet", "torch", "torchaudio",
                     "--index-url", "https://download.pytorch.org/whl/cpu"],
                    capture_output=True, timeout=300,
                )
                emit(LogEvent("CPU torch installed"))
        except Exception as exc:
            emit(LogEvent(f"Error: {exc}"))
            emit(LogEvent("Installing CPU torch..."))
            subprocess.run(
                [python, "-m", "pip", "install", "--quiet", "torch", "torchaudio",
                 "--index-url", "https://download.pytorch.org/whl/cpu"],
                capture_output=True, timeout=300,
            )
        finally:
            emit(SetBusyEvent(False))


class OllamaInstaller:
    """Скачивает и устанавливает Ollama, затем скачивает модель."""

    @staticmethod
    def check_ollama(
        enhancer: OllamaEnhancer,
        emit: Callable[[PipelineEvent], None],
        install_callback: Callable[[], None],
    ) -> None:
        """Асинхронная проверка Ollama — запускает установку при необходимости."""
        def check():
            ok, model = enhancer.is_available()
            if ok and model:
                emit(OllamaReadyEvent(True, True))
            elif ok and not model:
                emit(OllamaReadyEvent(True, False))
            else:
                emit(OllamaReadyEvent(False, False))
        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def install(
        enhancer: OllamaEnhancer,
        emit: Callable[[PipelineEvent], None],
    ) -> None:
        """Скачивает Ollama и модель в фоновом потоке."""
        emit(LogEvent("Скачивание Ollama..."))
        emit(SetBusyEvent(True))

        def install_worker():
            try:
                def on_progress(msg):
                    emit(LogEvent(msg))
                enhancer.install(progress_callback=on_progress)
                emit(OllamaReadyEvent(True, True))
            except Exception as exc:
                emit(LogEvent(f"Ошибка установки Ollama: {exc}"))
                emit(LogEvent("  Попробуйте: winget install Ollama"))
                emit(OllamaReadyEvent(False, False))
            finally:
                emit(SetBusyEvent(False))

        threading.Thread(target=install_worker, daemon=True).start()
