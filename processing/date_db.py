"""База знаний исторических дат.

Используется 4-м проходом улучшения (см. enhancer._pass_dates): для короткого
года («122») ищет в базе события, чей четырёхзначный год мог обрезаться до
этой записи, и у которых ключевое слово встречается в локальном контексте.
Одна однозначная кандидатура применяется детерминированно, без LLM.
"""

from __future__ import annotations

import json
from functools import lru_cache
from itertools import combinations
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dates.json"


@lru_cache(maxsize=1)
def _load_db() -> list[dict]:
    """Загружает базу дат из data/dates.json."""
    try:
        with open(_DB_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    return [entry for entry in data if isinstance(entry.get("year"), int)]


@lru_cache(maxsize=1)
def _subseq_index() -> dict[str, list[int]]:
    """Индекс: 2- и 3-символьные подпоследовательности года → индексы записей.

    Год 1202 при потере «0» внутри превращается в «122»; год 1185 при потере
    первой цифры — в «185». Индексируем ВСЕ подпоследовательности, чтобы
    отловить оба случая обрезания.
    """
    index: dict[str, list[int]] = {}
    for i, entry in enumerate(_load_db()):
        year = str(entry["year"])
        seen: set[str] = set()
        for length in (1, 2, 3):
            for idxs in combinations(range(len(year)), length):
                sub = "".join(year[j] for j in idxs)
                if sub and sub not in seen:
                    seen.add(sub)
                    index.setdefault(sub, []).append(i)
    return index


def find_year_candidates(year_str: str, context: str, full_years: list[int]) -> list[dict]:
    """Возвращает записи базы, подходящие для короткого года.

    Критерии:
    - строка короткого года — подпоследовательность четырёхзначного года
      в записи (т.е. год мог обрезаться до неё);
    - хотя бы одно ключевое слово записи есть в контексте (без учёта регистра);
    - год записи близок к эпохе текста (ориентир — четырёхзначные года текста),
      если ориентир есть.

    Кандидаты сортируются: точные совпадения по контексту и близкие годы первой.
    """
    index = _subseq_index()
    ctx = context.lower()
    year = str(year_str)
    epoch = _epoch_center(full_years)

    hits: list[tuple[float, dict]] = []
    for i in index.get(year, ()):
        entry = _load_db()[i]
        kw = [k.lower() for k in entry.get("keywords", [])]
        if not any(k in ctx for k in kw):
            continue
        entry_year = int(entry["year"])
        if epoch is not None and abs(entry_year - epoch) > 400:
            continue
        score = _candidate_score(entry_year, epoch, kw, ctx)
        hits.append((score, entry))
    hits.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in hits]


def _epoch_center(full_years: list[int]) -> int | None:
    """Характерная эпоха текста — середина диапазона его четырёхзначных годов."""
    if not full_years:
        return None
    return sum(full_years) // len(full_years)


def _candidate_score(entry_year: int, epoch: int | None, kw: list[str], ctx: str) -> float:
    """Оценивает кандидата: чем ближе год к эпохе и чем длиннее совпавшее
    ключевое слово, тем выше оценка."""
    score = 0.0
    if epoch is not None:
        score -= abs(entry_year - epoch) / 100.0
    for k in kw:
        if k in ctx:
            score += min(len(k), 24) / 24.0 * 2.0
    return score
