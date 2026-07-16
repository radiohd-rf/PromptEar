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
        logger.info(f"Установка зависимостей: {', '.join(missing)}")
        print(f"Установка: {', '.join(missing)}...")
        if "torch" in missing:
            subprocess.run(
                "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu",
                shell=True,
                timeout=300,
            )
        if "openai-whisper" in missing:
            subprocess.run("pip install openai-whisper", shell=True, timeout=300)


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
