"""Централизованная конфигурация PromptEar."""

import os
from pathlib import Path

# ── Пути данных ─────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PromptEar"
LOG_DIR = DATA_DIR / "logs"
FIRST_RUN_FLAG = DATA_DIR / ".initialized"
SETTINGS_FILE = DATA_DIR / "settings.json"



# ── Output ──────────────────────────────────────────────────────────────────
OUTPUT_FORMATS = ("docx", "txt")
DEFAULT_FORMAT = "docx"

# ── Audio / Video ───────────────────────────────────────────────────────────
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv", ".m4v", ".flv", ".ts"}
QUIET_THRESHOLD_DB = -20.0
FFMPEG_TIMEOUT = 30
PREPROCESS_SAMPLE_RATE = 16000
PREPROCESS_HIGHPASS_FREQ = 80  # убирает инфразвук
PREPROCESS_LOWPASS_FREQ = 8000  # убирает ВЧ-шум (речь 300-4000 Гц)

# ── Whisper ─────────────────────────────────────────────────────────────────
WHISPER_MODEL = "medium"

# ── Ollama / Qwen ───────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 300
OLLAMA_TEMPERATURE = 0.0

# ── Multi-pass enhancement ──────────────────────────────────────────────────
MULTI_PASS_MIN_RATIO = 0.6
MULTI_PASS_MAX_RATIO = 1.4
OLLAMA_NUM_PREDICT = -1  # unlimited output tokens
ENHANCER_CHUNK_SIZE = 3000  # symbols per chunk for long texts
SUMMARY_CHUNK_SIZE = 8000  # larger chunks for summary profile

# ── GPU ─────────────────────────────────────────────────────────────────────
NVIDIA_SMI_TIMEOUT = 5

# ── Error messages ──────────────────────────────────────────────────────────
ERROR_MESSAGES = {
    "ffmpeg": "FFmpeg не найден",
    "whisper": "Ошибка распознавания речи",
    "ollama": "Ошибка улучшения текста",
    "model": "Ошибка загрузки модели",
    "file": "Ошибка чтения файла",
    "save": "Ошибка сохранения результата",
    "unknown": "Неизвестная ошибка",
}
