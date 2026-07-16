"""Определение GPU и настройка PyTorch."""

import shutil
import subprocess

from config import NVIDIA_SMI_TIMEOUT


def has_nvidia_gpu() -> bool:
    """Проверяет наличие NVIDIA GPU через nvidia-smi."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, Exception):
        return False


def get_torch_device() -> str:
    """Возвращает 'cuda' или 'cpu'."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def torch_has_cuda() -> bool:
    """Проверяет, установлен ли torch с поддержкой CUDA."""
    try:
        import torch

        return torch.version.cuda is not None
    except (ImportError, AttributeError):
        return False


def get_install_command() -> str:
    """Возвращает команду установки torch."""
    if has_nvidia_gpu():
        return "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121"
    return "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"


def detect_and_report() -> dict:
    """Полная проверка GPU и возвращает отчёт."""
    gpu = has_nvidia_gpu()
    torch_cuda = torch_has_cuda()
    cuda_available = get_torch_device() == "cuda"

    return {
        "has_nvidia_gpu": gpu,
        "torch_cuda_installed": torch_cuda,
        "cuda_available": cuda_available,
        "device": "cuda" if cuda_available else "cpu",
        "need_install": gpu and not torch_cuda,
    }
