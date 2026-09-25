"""Каталог движков распознавания речи.

Единый источник правды для UI и сервера: список доступных бэкендов
(Whisper + GigaAM v3), их типы и соответствие внутренним моделям.
"""

from __future__ import annotations

from core import gigaam_models as gm
from core import whisper_models as wm

# backend id -> (kind, whisper_alias|gigaam_variant, label, size_mb, description)
# Порядок — как показывается в UI (GigaAM для русского впереди).
BACKENDS: list[tuple[str, str, str, str, int, str]] = [
    ("gigaam_e2e_rnnt", "gigaam", "e2e_rnnt", "GigaAM v3 e2e_rnnt", 428,
     "RNN-T с пунктуацией, лучшая точность русского"),
    ("whisper_base", "whisper", "base", "Whisper base", 145,
     "базовая, компромисс (по умолчанию)"),
    ("whisper_small", "whisper", "small", "Whisper small", 460,
     "точнее base, компромисс"),
    ("whisper_large", "whisper", "large-v3", "Whisper large-v3", 2900,
     "максимальная точность, русский+английский"),
]

DEFAULT_BACKEND = "whisper_base"

GROUP_LABELS = {
    "gigaam": "GigaAM v3 (русский)",
    "whisper": "Whisper (многоязычный)",
}


def get_backends() -> list[dict]:
    """Все бэкенды как список словарей для /api/models."""
    return [
        {
            "id": bid,
            "kind": kind,
            "group": kind,
            "variant": variant,
            "label": label,
            "size_mb": size,
            "description": desc,
        }
        for bid, kind, variant, label, size, desc in BACKENDS
    ]


def find(bid: str) -> dict | None:
    for entry in get_backends():
        if entry["id"] == bid:
            return entry
    return None


def is_backend(bid: str) -> bool:
    return find(bid) is not None


def kind_of(bid: str) -> str:
    entry = find(bid)
    return entry["kind"] if entry else "whisper"


def variant_of(bid: str) -> str:
    """Whisper-алиас или вариант GigaAM по id бэкенда."""
    for _bid, _kind, variant, *_ in BACKENDS:
        if _bid == bid:
            return variant
    return "base"


def is_installed(bid: str) -> bool:
    entry = find(bid)
    if not entry:
        return False
    if entry["kind"] == "whisper":
        return wm.is_installed(entry["variant"])
    return gm.is_installed(entry["variant"])


def installed_backend() -> str | None:
    for bid, *_ in BACKENDS:
        if is_installed(bid):
            return bid
    return None
