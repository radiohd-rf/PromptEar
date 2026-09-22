"""Определение GPU. Детект на ctranslate2 + nvidia-smi (без torch)."""

import shutil
import subprocess

from config import NVIDIA_SMI_TIMEOUT

_cached_report: dict | None = None


def has_nvidia_gpu() -> bool:
    """Проверяет наличие NVIDIA GPU через nvidia-smi (или wmic)."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=NVIDIA_SMI_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (subprocess.TimeoutExpired, Exception):
            pass
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_videocontroller", "get", "name"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "nvidia" in result.stdout.lower()
    except Exception:
        return False


def ctranslate2_cuda_devices() -> int:
    """Количество видеокарт, доступных ctranslate2 (0 — CUDA недоступна)."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def clear_gpu_cache() -> None:
    """Сбрасывает кеш detect_and_report."""
    global _cached_report
    _cached_report = None


def detect_and_report() -> dict:
    """Полная проверка GPU — однократный вызов, результат кешируется."""
    global _cached_report
    if _cached_report is not None:
        return _cached_report

    gpu = has_nvidia_gpu()
    ct2_cuda = ctranslate2_cuda_devices() > 0

    _cached_report = {
        "has_nvidia_gpu": gpu,
        "cuda_components": ct2_cuda,
        "cuda_available": ct2_cuda,
        "device": "cuda" if ct2_cuda else "cpu",
    }
    return _cached_report
