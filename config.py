"""Централизованная конфигурация PromptEar."""

import os
from pathlib import Path

# ── Пути данных ─────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PromptEar"
LOG_DIR = DATA_DIR / "logs"
FIRST_RUN_FLAG = DATA_DIR / ".initialized"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ── GUI ─────────────────────────────────────────────────────────────────────
WINDOW_WIDTH = 660
WINDOW_HEIGHT = 480

LIGHT_COLORS = {
    "bg": "#fff",
    "border": "#000",
    "fg": "#222",
    "ph": "#aaa",
    "status": "#555",
    "spinner": "#666",
    "btn_active_bg": "#ddd",
    "select_bg": "#eee",
    "select_fg": "#222",
    "error_bg": "#ffe0e0",
    "error_fg": "#c00",
}

DARK_COLORS = {
    "bg": "#1e1e1e",
    "border": "#444",
    "fg": "#e0e0e0",
    "ph": "#888",
    "status": "#aaa",
    "spinner": "#bbb",
    "btn_active_bg": "#333",
    "select_bg": "#264f78",
    "select_fg": "#fff",
    "error_bg": "#3a1515",
    "error_fg": "#ff6b6b",
}

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 12
FONT_SIZE_SMALL = 11

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
SPINNER_INTERVAL_MS = 120
QUEUE_POLL_MS = 200

# ── Output ──────────────────────────────────────────────────────────────────
OUTPUT_FORMATS = ("docx", "txt")
DEFAULT_FORMAT = "docx"

# ── Audio ───────────────────────────────────────────────────────────────────
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
QUIET_THRESHOLD_DB = -20.0
FFMPEG_TIMEOUT = 30
PREPROCESS_SAMPLE_RATE = 16000

# ── Whisper ─────────────────────────────────────────────────────────────────
WHISPER_MODEL = "medium"
WHISPER_LANGUAGE = "ru"

# ── Ollama / Qwen ───────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 300
OLLAMA_TEMPERATURE = 0.0

# ── Multi-pass enhancement ──────────────────────────────────────────────────
MULTI_PASS_MIN_RATIO = 0.6
MULTI_PASS_MAX_RATIO = 1.4

# ── GPU ─────────────────────────────────────────────────────────────────────
NVIDIA_SMI_TIMEOUT = 5

# ── User-friendly error map ─────────────────────────────────────────────────
ERROR_MESSAGES = {
    "No module named 'whisper'": "Библиотека Whisper не установлена. Запустите bootstrap.bat",
    "No module named 'torch'": "Библиотека Torch не установлена. Запустите bootstrap.bat",
    "Connection refused": "Ollama не запущена. Запустите Ollama и попробуйте снова",
    "ConnectionError": "Ollama не отвечает. Проверьте что она запущена",
    "FFMPEG not found": "ffmpeg не найден. Установите: winget install ffmpeg",
    "timed out": "Таймаут — операция заняла слишком много времени",
    "CUDA": "Ошибка CUDA. Попробуйте перезапустить приложение",
}
