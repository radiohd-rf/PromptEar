"""Каталог вариантов GigaAM v3 и управление установленной версией.

Каждый вариант хранится в models/gigaam/<variant>/ (скачивается через
DownloadManager). Одновременно может быть установлен только один вариант:
при смене каталоги остальных удаляются.
"""

from __future__ import annotations

from pathlib import Path

from config import MODELS_DIR

GIGAAM_REPO = "ai-sage/GigaAM-v3"
GIGAAM_DIR = MODELS_DIR / "gigaam"

# вариант -> (приблизительный размер в МБ, описание)
# По решению пользователя остаётся единственный вариант — e2e_rnnt
# (RNN-T с пунктуацией). Остальные (e2e_ctc/rnnt/ctc) из UI убраны.
GIGAAM_CATALOG: dict[str, tuple[int, str]] = {
    "e2e_rnnt": (428, "RNN-T с пунктуацией, лучшая точность"),
}


def get_catalog() -> dict[str, str]:
    """Варианты с человекочитаемым описанием (для UI/логов)."""
    return {variant: desc for variant, (_size, desc) in GIGAAM_CATALOG.items()}


def size_mb(variant: str) -> int:
    return int(GIGAAM_CATALOG.get(variant, (0, ""))[0] or 0)


def model_dir(variant: str) -> Path:
    """Каталог, куда скачивается заданный вариант GigaAM."""
    return GIGAAM_DIR / variant


def is_installed(variant: str) -> bool:
    """Вариант установлен, если в его каталоге есть pytorch_model.bin."""
    return (model_dir(variant) / "pytorch_model.bin").exists()


def installed_variant() -> str | None:
    for variant in GIGAAM_CATALOG:
        if is_installed(variant):
            return variant
    return None


def remove_others(keep_variant: str) -> None:
    keep = model_dir(keep_variant)
    GIGAAM_DIR.mkdir(parents=True, exist_ok=True)
    for folder in GIGAAM_DIR.iterdir():
        if folder != keep and folder.is_dir():
            import shutil

            shutil.rmtree(folder, ignore_errors=True)


def remove_all() -> None:
    """Удаляет все варианты GigaAM (при переключении на Whisper)."""
    import shutil

    GIGAAM_DIR.mkdir(parents=True, exist_ok=True)
    for folder in GIGAAM_DIR.iterdir():
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
