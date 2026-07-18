"""Tests for GPU detection."""

from unittest.mock import MagicMock

from utils.gpu import detect_and_report, get_torch_device, has_nvidia_gpu, torch_has_cuda


def test_has_nvidia_true(mocker):
    mock_run = mocker.patch("utils.gpu.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="NVIDIA RTX 3080\n")
    mocker.patch("utils.gpu.shutil.which", return_value=r"C:\nvidia-smi.exe")
    assert has_nvidia_gpu() is True


def test_has_nvidia_false(mocker):
    from subprocess import TimeoutExpired

    mocker.patch("utils.gpu.shutil.which", return_value=r"C:\nvidia-smi.exe")
    mocker.patch("utils.gpu.subprocess.run", side_effect=TimeoutExpired("cmd", 5))
    assert has_nvidia_gpu() is False


def test_has_nvidia_timeout(mocker):
    from subprocess import TimeoutExpired

    mocker.patch("utils.gpu.shutil.which", return_value=r"C:\nvidia-smi.exe")
    mocker.patch("utils.gpu.subprocess.run", side_effect=TimeoutExpired("cmd", 5))
    assert has_nvidia_gpu() is False


def test_torch_cuda_installed(mocker):
    mock_torch = MagicMock()
    mock_torch.version.cuda = "12.1"
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    assert torch_has_cuda() is True


def test_torch_cuda_missing(mocker):
    mock_torch = MagicMock()
    mock_torch.version.cuda = None
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    assert torch_has_cuda() is False


def test_get_torch_device_cpu(mocker):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    assert get_torch_device() == "cpu"


def test_get_torch_device_cuda(mocker):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    assert get_torch_device() == "cuda"


def test_detect_and_report_needs_install(mocker):
    mocker.patch("utils.gpu.shutil.which", return_value=r"C:\nvidia-smi.exe")
    mocker.patch(
        "utils.gpu.subprocess.run", return_value=MagicMock(returncode=0, stdout="NVIDIA RTX 3080\n")
    )
    mock_torch = MagicMock()
    mock_torch.version.cuda = None
    mock_torch.cuda.is_available.return_value = False
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    report = detect_and_report()
    assert report["has_nvidia_gpu"] is True
    assert report["torch_cuda_installed"] is False
    assert report["need_install"] is True


def test_detect_and_report_no_gpu(mocker):
    from subprocess import TimeoutExpired

    mocker.patch("utils.gpu.shutil.which", return_value=r"C:\nvidia-smi.exe")
    mocker.patch("utils.gpu.subprocess.run", side_effect=TimeoutExpired("cmd", 5))
    mock_torch = MagicMock()
    mock_torch.version.cuda = None
    mock_torch.cuda.is_available.return_value = False
    mocker.patch.dict("sys.modules", {"torch": mock_torch})
    report = detect_and_report()
    assert report["has_nvidia_gpu"] is False
    assert report["need_install"] is False
