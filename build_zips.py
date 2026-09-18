#!/usr/bin/env python3
"""Сборка двух zip-дистрибутивов: CPU и CUDA.

Запуск: python build_zips.py [cpu] [cu126]
По умолчанию собираются обе версии.

Каждый zip содержит:
  - Исходный код (web/, core/, processing/, utils/, assets/)
  - bootstrap.bat, run.bat, run.pyw, main.py, config.py, requirements.txt
  - wheels/ — torch, torchaudio и все зависимости (pip download)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

VARIANTS: dict[str, str] = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cu126": "https://download.pytorch.org/whl/cu126",
}

SOURCE_FILES = [
    "main.py",
    "config.py",
    "bootstrap.bat",
    "run.bat",
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

REQUIRED_PACKAGES = ["torch", "torchaudio"]
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
LLAMA_GGUF = Path(r"G:\models\e2b\gemma-4-E2B-it-UD-Q4_K_XL.gguf")


def _excluded(path: Path, rel: Path) -> bool:
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return True
    return rel.suffix in EXCLUDE_SUFFIXES


def _pip_download(index_url: str, dest: Path) -> None:
    """Скачивает torch + torchaudio с зависимостями в dest."""
    # Перенаправляем TEMP на диск сборки (C: может быть мал для torch 2.5 GB)
    tmpdir = str(dest.parent / "_pip_temp")
    os.makedirs(tmpdir, exist_ok=True)
    env = {**os.environ, "TEMP": tmpdir, "TMP": tmpdir}
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        *REQUIRED_PACKAGES,
        "--index-url",
        index_url,
        "--trusted-host",
        "download.pytorch.org",
        "--only-binary=:all:",
        "-d",
        str(dest),
    ]
    print(f"  pip download -> {dest}")
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


def _embed_gguf(dest: Path) -> None:
    """Копирует gemma-4-E2B-it GGUF в dest/models/llm (если файл доступен)."""
    llm_dir = dest / "models" / "llm"
    target = llm_dir / LLAMA_GGUF.name
    if target.exists():
        print("  GGUF уже есть")
        return
    if LLAMA_GGUF.exists():
        llm_dir.mkdir(parents=True, exist_ok=True)
        size_mb = LLAMA_GGUF.stat().st_size / 1024 / 1024
        print(f"  Копирование GGUF ({size_mb:.0f} MB)...")
        shutil.copy2(LLAMA_GGUF, target)
    else:
        print("  ⚠ GGUF gemma-4-E2B не найден — модель не встроена (скачается на лету)")


def build_variant(name: str, index_url: str) -> None:
    print(f"\n=== Сборка {name.upper()} ===")

    build_dir = ROOT / f"_build_{name}"
    wheels_dir = build_dir / "wheels"
    zip_path = ROOT / f"PromptEar-v0.13.0-{name}.zip"

    # Очистка
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    wheels_dir.mkdir()

    # 1. Скачать wheels
    _pip_download(index_url, wheels_dir)

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
        ico = str(ROOT / "di.ico")
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

    # 5. Встроить llama.cpp (только CPU) и модель gemma-4-E2B
    if name == "cpu":
        _download_llama_cpp(build_dir)
        _embed_gguf(build_dir)

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
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(VARIANTS.keys())
    for name in targets:
        if name not in VARIANTS:
            print(f"Неизвестный вариант: {name}. Допустимые: {', '.join(VARIANTS.keys())}")
            continue
        build_variant(name, VARIANTS[name])
    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
