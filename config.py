"""Централизованная конфигурация PromptEar."""

import os
from pathlib import Path
from typing import Final

# ── Пути данных ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
# Настройки и логи хранятся в папке приложения (портативность): скопировал
# папку — настройки, модели и кеши приехали с ней. В %APPDATA% ничего не пишем.
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
FIRST_RUN_FLAG = DATA_DIR / ".initialized"
SETTINGS_FILE = DATA_DIR / "settings.json"
# Флаг "открыть настройки при следующем старте" (ставится перед перезапуском
# программы и снимается первым же /api/startup после него).
SETTINGS_OPEN_FLAG = DATA_DIR / "open_settings.flag"
TEMP_DIR: Final[Path] = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

# Waitress буферизует multipart-загрузки во временные файлы через tempfile,
# который по умолчанию пишет в системный %TEMP% (обычно диск C:). Если на C:
# нет места, upload падает с разрывом соединения (Errno 28 в tempfile) и
# транскрибация не стартует. Переопределяем на собственный temp/ внутри
# портативной папки — место гарантировано вместе с приложением.
os.environ["TMPDIR"] = str(TEMP_DIR)
os.environ["TEMP"] = str(TEMP_DIR)
os.environ["TMP"] = str(TEMP_DIR)

# Кэши моделей внутри портативной папки (не BrokenCache WebView2 — диск)
MODELS_DIR = BASE_DIR / "models"
HF_HOME = MODELS_DIR / "hf"
# huggingface_hub (метаданные/модели) пишет в папку программы, а не в
# C:\Users\<user>\.cache\huggingface. setdefault — уважаем явный системный HF_HOME.
os.environ.setdefault("HF_HOME", str(HF_HOME))
CT2_CACHE = MODELS_DIR / "ct2"
LLM_MODEL_DIR = MODELS_DIR / "llm"
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

# Борьба с галлюцинациями-петлями («... ... ...» на длинном аудио, лекциях):
# condition_on_previous_text=True (дефолт Whisper) возвращает декодеру его же
# предыдущий вывод, и при длинной записи модель начинает зацикливать мусор.
# False — петли в основном уходят (контекст берётся заново с VAD-старта).
WHISPER_CONDITION_ON_PREVIOUS_TEXT = False
# VAD-порог короче дефолтного (2000 мс): паузы режутся на сегменты чаще, значит
# меньше «пустого» аудио на входе декодера, где и рождаются галлюцинации.
WHISPER_VAD_MIN_SILENCE_MS = 500
WHISPER_VAD_SPEECH_PAD_MS = 300

# ── LLM-движок ──────────────────────────────────────────────────────────────
LLM_ENGINE = "llama"

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

# Сборки llama.cpp (bin-win-cpu-x64.zip идёт в комплекте сборки приложения;
# bin-win-cuda-13.4-x64.zip докачивается автоматически при включении GPU;
# cudart-...·zip — CUDA-рантайм (cudart/cublas), без него ggml-cuda не грузится).
def _llama_release_url(suffix: str) -> str:
    return (
        f"https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-{suffix}.zip"
    )


LLAMA_CPU_URL = _llama_release_url("bin-win-cpu-x64")
LLAMA_CUDA_URL = _llama_release_url("bin-win-cuda-13.4-x64")
LLAMA_CUDART_URL = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_RELEASE}/cudart-llama-bin-win-cuda-13.4-x64.zip"
)

# Слоёв модели выгружаемых на GPU при ИИ-улучшении (ngl). Cpu-остаток ≈ 0.
LLAMA_GPU_LAYERS = 99

# ── Multi-pass enhancement ──────────────────────────────────────────────────
MULTI_PASS_MIN_RATIO = 0.6
MULTI_PASS_MAX_RATIO = 1.4
ENHANCER_CHUNK_SIZE = 3000  # symbols per chunk for long texts

# ── GPU ─────────────────────────────────────────────────────────────────────
NVIDIA_SMI_TIMEOUT = 5

# ── Выгрузка моделей при простое ──────────────────────────────────────────
MODEL_IDLE_TIMEOUT_SEC = 300  # 5 минут без обращений — гасим движки
MODEL_IDLE_CHECK_SEC = 60  # как часто сторож проверяет простой
# Нет прогресса транскрибации дольше — считаем зависшей (fail fast вместо
# вечного спина). Чанк/сегмент идут секундами даже на CPU, запас огромный.
TRANSCRIBE_STALL_TIMEOUT_SEC = 600

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
