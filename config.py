"""Централизованная конфигурация PromptEar."""

# ── GUI ───────────────────────────────────────────────────────────────────
WINDOW_WIDTH = 660
WINDOW_HEIGHT = 480

COLORS = {
    "white": "#fff",
    "border": "#000",
    "fg": "#222",
    "ph": "#aaa",
    "status": "#555",
    "spinner": "#666",
    "btn_active_bg": "#ddd",
}

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 12
FONT_SIZE_SMALL = 11

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
SPINNER_INTERVAL_MS = 120
QUEUE_POLL_MS = 200

# ── Output ────────────────────────────────────────────────────────────────
OUTPUT_FORMATS = ("docx", "txt")
DEFAULT_FORMAT = "docx"

# ── Audio ─────────────────────────────────────────────────────────────────
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
QUIET_THRESHOLD_DB = -20.0
FFMPEG_TIMEOUT = 30
PREPROCESS_SAMPLE_RATE = 16000

# ── Whisper ───────────────────────────────────────────────────────────────
WHISPER_MODEL = "medium"
WHISPER_LANGUAGE = "ru"

# ── Ollama / Qwen ─────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 300
OLLAMA_TEMPERATURE = 0.0

# ── Multi-pass enhancement ────────────────────────────────────────────────
MULTI_PASS_MIN_RATIO = 0.6
MULTI_PASS_MAX_RATIO = 1.4

# ── GPU ───────────────────────────────────────────────────────────────────
NVIDIA_SMI_TIMEOUT = 5
