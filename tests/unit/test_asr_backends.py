"""Тесты каталога ASR-бэкендов (core/asr_backends.py)."""

import core.asr_backends as ab
import core.gigaam_models as gm
import core.whisper_models as wm


def test_backends_order_and_default() -> None:
    ids = [b[0] for b in ab.BACKENDS]
    assert ids[0] == "gigaam_e2e_rnnt"
    assert "whisper_base" in ids
    assert ab.DEFAULT_BACKEND == "whisper_base"


def test_get_backends_shape() -> None:
    items = ab.get_backends()
    assert len(items) == len(ab.BACKENDS)
    for item in items:
        assert set(item) == {"id", "kind", "group", "variant", "label", "size_mb", "description"}
    assert ab.get_backends()[0]["id"] == "gigaam_e2e_rnnt"


def test_find_and_is_backend() -> None:
    entry = ab.find("whisper_base")
    assert entry is not None and entry["kind"] == "whisper"
    assert ab.find("нет_такого") is None
    assert ab.is_backend("whisper_base") is True
    assert ab.is_backend("нет_такого") is False


def test_kind_of() -> None:
    assert ab.kind_of("gigaam_e2e_rnnt") == "gigaam"
    assert ab.kind_of("whisper_small") == "whisper"
    assert ab.kind_of("нет_такого") == "whisper"


def test_variant_of() -> None:
    assert ab.variant_of("gigaam_e2e_rnnt") == "e2e_rnnt"
    assert ab.variant_of("whisper_large") == "large-v3"
    assert ab.variant_of("нет_такого") == "base"


def test_is_installed_unknown_backend(monkeypatch) -> None:
    monkeypatch.setattr(wm, "is_installed", lambda _alias: False)
    monkeypatch.setattr(gm, "is_installed", lambda _variant: False)
    assert ab.is_installed("нет_такого") is False


def test_is_installed_whisper_backend(monkeypatch) -> None:
    monkeypatch.setattr(wm, "is_installed", lambda alias: alias == "base")
    assert ab.is_installed("whisper_base") is True
    assert ab.is_installed("whisper_small") is False


def test_is_installed_gigaam_backend(monkeypatch) -> None:
    monkeypatch.setattr(gm, "is_installed", lambda variant: variant == "e2e_rnnt")
    assert ab.is_installed("gigaam_e2e_rnnt") is True


def test_installed_backend_prefers_first_installed(monkeypatch) -> None:
    monkeypatch.setattr(gm, "is_installed", lambda _variant: True)
    monkeypatch.setattr(wm, "is_installed", lambda _alias: True)
    assert ab.installed_backend() == "gigaam_e2e_rnnt"


def test_installed_backend_skips_not_installed_first(monkeypatch) -> None:
    monkeypatch.setattr(gm, "is_installed", lambda _variant: False)
    monkeypatch.setattr(wm, "is_installed", lambda _alias: True)
    assert ab.installed_backend() == "whisper_base"
