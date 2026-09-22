"""Централизованная конфигурация PromptEar."""

import os
from pathlib import Path
from typing import Final

# ── Пути данных ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PromptEar"
LOG_DIR = DATA_DIR / "logs"
FIRST_RUN_FLAG = DATA_DIR / ".initialized"
SETTINGS_FILE = DATA_DIR / "settings.json"
TEMP_DIR: Final[Path] = BASE_DIR / "temp"

# Кэши моделей внутри портативной папки (не BrokenCache WebView2 — диск)
MODELS_DIR = BASE_DIR / "models"
HF_HOME = MODELS_DIR / "hf"
# huggingface_hub (SAGE-модель и метаданные) пишет в папку программы, а не в
# C:\Users\<user>\.cache\huggingface. setdefault — уважаем явный системный HF_HOME.
os.environ.setdefault("HF_HOME", str(HF_HOME))
CT2_CACHE = MODELS_DIR / "ct2"
LLM_MODEL_DIR = MODELS_DIR / "llm"
SAGE_MODEL_DIR = MODELS_DIR / "sage"
LLAMA_DIR = BASE_DIR / "llama"

# ── Output ──────────────────────────────────────────────────────────────────
OUTPUT_FORMATS = ("docx", "txt", "srt", "vtt", "md")
DEFAULT_FORMAT = "docx"

# ── Enhance mode ────────────────────────────────────────────────────────────
# none — только транскрибация; auto — улучшать сразу; ask — кнопка «Улучшить с ИИ»
ENHANCE_MODES = ("none", "auto", "ask")
DEFAULT_ENHANCE_MODE = "auto"

# ── Audio / Video ───────────────────────────────────────────────────────────
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv", ".m4v", ".flv", ".ts"}
QUIET_THRESHOLD_DB = -20.0
FFMPEG_TIMEOUT = 30
PREPROCESS_SAMPLE_RATE = 16000
PREPROCESS_HIGHPASS_FREQ = 80  # убирает инфразвук
PREPROCESS_LOWPASS_FREQ = 8000  # убирает ВЧ-шум (речь 300-4000 Гц)

# ── Whisper ─────────────────────────────────────────────────────────────────
# Модель по умолчанию (устанавливается из settings.json; каталог в
# core/whisper_models.py). tiny — самая лёгкая, вшивается в сборку.
WHISPER_DEFAULT_MODEL = "base"

# ── LLM-движок ──────────────────────────────────────────────────────────────
LLM_ENGINE = "llama"  # "llama" | "sage"

# ── llama.cpp (bin-win-cpu-x64.zip, llama-server.exe) ──────────────────────
LLAMA_RELEASE = "b11034"  # версия релиза, см. github.com/ggml-org/llama.cpp/releases
LLAMA_SERVER_PORT = int(os.environ.get("PROMPTEAR_LLM_PORT", "8080"))

LLM_BASE_URL = f"http://127.0.0.1:{LLAMA_SERVER_PORT}"
LLM_MODEL = "gemma-4-E2B-it"
LLM_MODEL_FILENAME = "gemma-4-E2B-it-UD-Q4_K_XL.gguf"
LLM_TIMEOUT = 600
LLM_TEMPERATURE = 0.0
LLM_NUM_PREDICT = -1
LLM_CONTEXT = 4096
LLM_RETRIES = 1  # повтор запроса при таймауте (фикс бага #15)
LLM_MODEL_PATH = LLM_MODEL_DIR / LLM_MODEL_FILENAME

# URL прямого скачивания GGUF (HF-хостинг).
# Файл: gemma-4-E2B-it-UD-Q4_K_XL.gguf (3.0 ГБ), Apache-2.0.
GGUF_URL = (
    "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/"
    "gemma-4-E2B-it-UD-Q4_K_XL.gguf"
)

# ── SAGE (FRED-T5-1.7B, однопроходный корректор) ───────────────────────────
SAGE_HF_REPO = "ai-forever/sage-v1.1.0"
SAGE_MAX_CHARS = 1000  # вход T5 ≤512 токенов → чанк меньше LLM-чанка

# ── Multi-pass enhancement ──────────────────────────────────────────────────
MULTI_PASS_MIN_RATIO = 0.6
MULTI_PASS_MAX_RATIO = 1.4
ENHANCER_CHUNK_SIZE = 3000  # symbols per chunk for long texts

# ── GPU ─────────────────────────────────────────────────────────────────────
NVIDIA_SMI_TIMEOUT = 5

# ── Error messages ──────────────────────────────────────────────────────────
ERROR_MESSAGES = {
    "ffmpeg": "FFmpeg не найден",
    "whisper": "Ошибка распознавания речи",
    "llm": "Ошибка улучшения текста",
    "model": "Ошибка загрузки модели",
    "file": "Ошибка чтения файла",
    "save": "Ошибка сохранения результата",
    "unknown": "Неизвестная ошибка",
}
