#!/usr/bin/env python3
"""PromptEar — точка входа."""

import importlib.util
import subprocess
import sys
import tkinter as tk

from app import PromptEarApp
from utils.logger import get_logger

logger = get_logger()


def ensure_deps():
    """Проверяет и устанавливает зависимости если нужно."""
    missing = []
    if importlib.util.find_spec("torch") is None:
        missing.append("torch")
    if importlib.util.find_spec("whisper") is None:
        missing.append("openai-whisper")

    if missing:
        python = sys.executable
        logger.info(f"Установка зависимостей: {', '.join(missing)}")
        if sys.stdout:
            print(f"Установка: {', '.join(missing)}...")
        if "torch" in missing:
            subprocess.run(
                [
                    python,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "torch",
                    "torchaudio",
                    "--index-url",
                    "https://download.pytorch.org/whl/cu126",
                    "--trusted-host",
                    "download.pytorch.org",
                    "torch==2.11.0+cu126",
                    "torchaudio==2.11.0+cu126",
                ],
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if "openai-whisper" in missing:
            subprocess.run(
                [python, "-m", "pip", "install", "--quiet", "openai-whisper"],
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )


ensure_deps()

HAS_DND = importlib.util.find_spec("tkinterdnd2") is not None
if HAS_DND:
    from tkinterdnd2 import TkinterDnD


def main():
    try:
        root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        app = PromptEarApp(root)
        app.run()
    except Exception as exc:
        logger.critical(f"Критическая ошибка: {exc}", exc_info=True)
        from tkinter import messagebox

        messagebox.showerror(
            "Критическая ошибка",
            f"PromptEar не может запуститься.\n\n{exc}\n\n"
            f"Подробнее: %APPDATA%\\PromptEar\\logs\\app.log",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
