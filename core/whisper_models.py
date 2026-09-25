"""Каталог моделей Whisper и управление установленной версией.

Каждая модель хранится в models/ct2/<repo_name>/ (скачивается через
download_root=models/ct2). Одновременно установлена только одна модель:
при смене каталоги остальных версий удаляются (см. DownloadManager).
"""

from __future__ import annotations

from pathlib import Path

from config import CT2_CACHE

# алиас -> (HF repo_id, приблизительный размер в МБ, описание)
# По решению пользователя каталог: base (по умолчанию), small и large-v3.
WHISPER_CATALOG: dict[str, tuple[str, int, str]] = {
    "base": ("Systran/faster-whisper-base", 145, "базовая, компромисс"),
    "small": ("Systran/faster-whisper-small", 460, "точнее base, компромисс"),
    "large-v3": ("Systran/faster-whisper-large-v3", 2900, "самая мощная, иностранная речь"),
}


def get_catalog() -> dict[str, str]:
    """Возвращает алиасы с человекочитаемым описанием (для UI)."""
    return {alias: desc for alias, (_repo, _size, desc) in WHISPER_CATALOG.items()}


def resolve(alias: str) -> str:
    """Возвращает HF repo_id по алиасу (дефолт — base)."""
    entry = WHISPER_CATALOG.get(alias)
    return entry[0] if entry else WHISPER_CATALOG["base"][0]


def size_mb(alias: str) -> int:
    entry = WHISPER_CATALOG.get(alias)
    return entry[1] if entry else 0


def model_dir(alias: str) -> Path:
    """Каталог, куда скачивается заданная модель."""
    return CT2_CACHE / resolve(alias).split("/")[-1]


def is_installed(alias: str) -> bool:
    """Модель считается установленной, если в её каталоге есть model.bin."""
    marker = model_dir(alias) / "model.bin"
    return marker.exists()


def installed_model() -> str | None:
    """Возвращает алиас установленной модели или None."""
    for alias in WHISPER_CATALOG:
        if is_installed(alias):
            return alias
    return None


def remove_others(keep_alias: str) -> None:
    """Удаляет каталоги всех моделей, кроме keep_alias."""
    keep_dir = model_dir(keep_alias)
    CT2_CACHE.mkdir(parents=True, exist_ok=True)
    for folder in CT2_CACHE.iterdir():
        if folder != keep_dir and folder.is_dir():
            import shutil

            shutil.rmtree(folder, ignore_errors=True)


def remove_all() -> None:
    """Удаляет каталоги всех моделей Whisper (при переключении на GigaAM)."""
    import shutil

    CT2_CACHE.mkdir(parents=True, exist_ok=True)
    for folder in CT2_CACHE.iterdir():
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
