"""Персистентные настройки приложения (settings.json в папке приложения)."""

import json

from config import SETTINGS_FILE

DEFAULT_SETTINGS = {
    "whisper_model": "base",
    "asr_backend": "whisper_base",
    "use_gpu": False,
    "output_format": "docx",
    "timestamps": False,
    "ai_enabled": False,
    "llm_port": 8080,
    # Гостевые флаги «не показывать приветствие/инструкцию». Храним на сервере,
    # а не в localStorage: UI открывается на случайном порту 127.0.0.1:<N>,
    # origin меняется при каждом рестарте и веб-хранилище обнуляется.
    "ui_welcome_seen": False,
    "ui_help_seen": False,
}


def load() -> dict:
    """Читает settings.json и возвращает словарь (недостающие ключи — дефолтные)."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return settings


def save(patch: dict | None = None) -> dict:
    """Обновляет настройки патчем и пишет на диск. Возвращает актуальные настройки."""
    settings = load()
    if patch:
        settings.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # настройки остаются в памяти, диск недоступен
    return settings
