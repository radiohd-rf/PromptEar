#!/usr/bin/env python3
"""Сборка единого CPU-дистрибутива PromptEar.

Запуск: python build_zips.py

Zip содержит:
  - Исходный код (web/, core/, processing/, utils/, assets/)
  - bootstrap.bat, run.bat, run.pyw, main.py, config.py, requirements.txt
  - wheels/ — torch (CPU), torchaudio и все зависимости (pip download)
  - ffmpeg.exe, llama.cpp (win-cpu)
  - Модель Whisper base (встроена в models/ct2, остальные качаются на лету)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TORCH_INDEX = "https://download.pytorch.org/whl/cpu"

SOURCE_FILES = [
    "main.py",
    "config.py",
    "bootstrap.bat",
    "run.bat",
    "hidden.vbs",
    "run.pyw",
    "requirements.txt",
    "launcher.cs",
]

SOURCE_DIRS = [
    "web",
    "core",
    "processing",
    "utils",
    "assets",
]

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_DIRS = {"__pycache__", ".git", ".github", ".pytest_cache", ".ruff_cache"}

TORCH_PACKAGES = ["torch", "torchaudio"]

PIP_PACKAGES = [
    "flask",
    "pywebview",
    "faster-whisper",
    "nvidia-cublas-cu12",
    "Pillow",
    "python-docx",
    "requests",
]

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _excluded(path: Path, rel: Path) -> bool:
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return True
    return rel.suffix in EXCLUDE_SUFFIXES


def _pip_download_torch(dest: Path) -> None:
    """Скачивает torch + torchaudio (CPU) с зависимостями из CPU-индекса."""
    tmpdir = str(dest.parent / "_pip_temp")
    os.makedirs(tmpdir, exist_ok=True)
    env = {**os.environ, "TEMP": tmpdir, "TMP": tmpdir}
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        *TORCH_PACKAGES,
        "--index-url",
        TORCH_INDEX,
        "--trusted-host",
        "download.pytorch.org",
        "--only-binary=:all:",
        "-d",
        str(dest),
    ]
    print(f"  pip download torch (cpu) -> {dest}")
    subprocess.run(cmd, check=True, env=env)
    whls = list(dest.glob("*.whl"))
    print(f"  Скачано {len(whls)} wheel-файлов "
          f"({sum(f.stat().st_size for f in whls) / 1024 / 1024:.0f} MB)")


def _pip_download_packages(dest: Path) -> None:
    """Скачивает flask, faster-whisper и остальные зависимости из PyPI."""
    tmpdir = str(dest.parent / "_pip_temp")
    os.makedirs(tmpdir, exist_ok=True)
    env = {**os.environ, "TEMP": tmpdir, "TMP": tmpdir}
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        *PIP_PACKAGES,
        "-d",
        str(dest),
    ]
    print(f"  pip download packages -> {dest}")
    subprocess.run(cmd, check=True, env=env)
    whls = list(dest.glob("*.whl"))
    total_mb = sum(f.stat().st_size for f in whls) / 1024 / 1024
    print(f"  Скачано {len(whls)} wheel-файлов ({total_mb:.0f} MB)")


def _download_ffmpeg(dest: Path) -> None:
    """Скачивает ffmpeg.exe и кладёт в dest."""
    ffmpeg_exe = dest / "ffmpeg.exe"
    if ffmpeg_exe.exists():
        print("  ffmpeg.exe уже есть")
        return
    print("  Скачивание ffmpeg...")
    zip_path = dest.parent / "ffmpeg.zip"
    subprocess.run(
        ["curl", "-sS", "-L", "-o", str(zip_path), FFMPEG_URL],
        check=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(tmp)
        for f in Path(tmp).rglob("ffmpeg.exe"):
            shutil.copy2(f, ffmpeg_exe)
            break
    zip_path.unlink()
    size_mb = ffmpeg_exe.stat().st_size / 1024 / 1024
    print(f"  ffmpeg.exe ({size_mb:.0f} MB)")


def _download_llama_cpp(dest: Path) -> None:
    """Скачивает llama.cpp (win-cpu) и разворачивает в dest/llama целиком.

    Важно: llama-server.exe в новых сборках — стаб, вся логика в DLL рядом,
    поэтому копируется весь каталог.
    """
    server_exe = dest / "llama" / "llama-server.exe"
    if server_exe.exists():
        print("  llama-server.exe уже есть")
        return
    print("  Скачивание llama.cpp...")
    zip_path = dest.parent / "llama.zip"
    release = os.environ.get("LLAMA_RELEASE", "b11034")
    url = (
        f"https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{release}/llama-{release}-bin-win-cpu-x64.zip"
    )
    subprocess.run(["curl", "-sS", "-L", "-o", str(zip_path), url], check=True)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(tmp)
        src_dir = (Path(tmp) / "llama-server.exe").parent
        if not (src_dir / "llama-server.exe").exists():
            raise RuntimeError("llama-server.exe не найден в архиве llama.cpp")
        (dest / "llama").mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            shutil.copy2(item, dest / "llama" / item.name)
    zip_path.unlink()


def _embed_whisper_tiny(dest: Path) -> None:
    """Встраивает самую лёгкую модель Whisper (base) в dest/models/ct2."""
    from core.downloader import download_whisper_model

    alias = "base"
    target = dest / "models" / "ct2" / alias
    if (target / "model.bin").exists():
        print(f"  Модель {alias} уже встроена")
        return
    print(f"  Встраивание модели Whisper {alias}...")
    target.mkdir(parents=True, exist_ok=True)
    download_whisper_model(alias, target)
    print(f"  Модель {alias} готова")


def build_dist() -> None:
    build_dir = ROOT / "_build_cpu"
    wheels_dir = build_dir / "wheels"
    zip_path = ROOT / "PromptEar-v0.13.0-cpu.zip"

    # Очистка
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    wheels_dir.mkdir()

    # 1. Скачать wheels (torch CPU из variant-индекса, остальное из PyPI)
    _pip_download_torch(wheels_dir)
    _pip_download_packages(wheels_dir)

    # 2. Скопировать исходники
    print("  Копирование исходников...")
    for fname in SOURCE_FILES:
        src = ROOT / fname
        if src.exists():
            shutil.copy2(src, build_dir / fname)
        else:
            print(f"  ⚠ {fname} не найден, пропускаю")

    for dname in SOURCE_DIRS:
        src = ROOT / dname
        if src.exists():
            dst = build_dir / dname
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".git", ".github", ".pytest_cache", ".ruff_cache"
                ),
            )

    # 3. Скомпилировать launcher.cs → Запустить PromptEar.exe
    print("  Компиляция лаунчера...")
    _windir = os.environ.get("WINDIR", "C:\\Windows")
    csc_paths = [
        Path(_windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        Path(_windir) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    csc = None
    for p in csc_paths:
        if p.exists():
            csc = str(p)
            break
    if csc:
        ico = str(ROOT / "assets" / "icon.ico")
        if not Path(ico).exists():
            ico = str(ROOT / "assets" / "icon_titlebar.ico")
        refs = [
            "System.dll",
            "System.Windows.Forms.dll",
        ]
        cmd = [csc, "/target:winexe", f"/win32icon:{ico}", "/nologo"]
        for r in refs:
            cmd.append(f"/reference:{r}")
        cmd.append(f"/out:{build_dir / 'Запустить PromptEar.exe'}")
        cmd.append(str(ROOT / "launcher.cs"))
        subprocess.run(cmd, check=True)
        print("  Лаунчер готов")
    else:
        print("  ⚠ csc.exe не найден, лаунчер не скомпилирован")

    # 4. Скачать ffmpeg.exe
    _download_ffmpeg(build_dir)

    # 5. Встроить llama.cpp и модель Whisper base
    _download_llama_cpp(build_dir)
    _embed_whisper_tiny(build_dir)

    # 6. Создать zip
    print(f"  Создание {zip_path.name}...")
    total = 0
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for file in build_dir.rglob("*"):
            if file.is_file():
                rel = file.relative_to(build_dir)
                if _excluded(file, rel):
                    continue
                zf.write(str(file), str(rel))
                total += 1
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  Готово: {zip_path.name} ({total} файлов, {size_mb:.0f} MB)")

    # Очистка
    shutil.rmtree(build_dir)


def main() -> None:
    build_dist()
    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
