"""Тесты каталога моделей Whisper (core/whisper_models.py)."""

import core.whisper_models as wm
from config import CT2_CACHE


def test_catalog_has_expected_models() -> None:
    assert set(wm.WHISPER_CATALOG) == {"base", "small", "large-v3"}


def test_get_catalog_maps_alias_to_desc() -> None:
    catalog = wm.get_catalog()
    assert catalog["base"] == "базовая, компромисс"
    assert catalog["large-v3"] == "самая мощная, иностранная речь"


def test_resolve_known_alias() -> None:
    assert wm.resolve("base") == "Systran/faster-whisper-base"
    assert wm.resolve("small") == "Systran/faster-whisper-small"


def test_resolve_unknown_alias_falls_back_to_base() -> None:
    assert wm.resolve("nonexistent") == "Systran/faster-whisper-base"


def test_size_mb_known_and_unknown() -> None:
    assert wm.size_mb("base") == 145
    assert wm.size_mb("small") == 460
    assert wm.size_mb("nope") == 0


def test_model_dir_uses_last_repo_part() -> None:
    assert wm.model_dir("base") == CT2_CACHE / "faster-whisper-base"


def test_is_installed_depends_on_model_bin(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "CT2_CACHE", tmp_path)
    assert wm.is_installed("base") is False
    (tmp_path / "faster-whisper-base").mkdir(parents=True)
    (tmp_path / "faster-whisper-base" / "model.bin").write_bytes(b"x")
    assert wm.is_installed("base") is True


def test_installed_model_returns_first_installed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "CT2_CACHE", tmp_path)
    assert wm.installed_model() is None
    (tmp_path / "faster-whisper-small").mkdir(parents=True)
    (tmp_path / "faster-whisper-small" / "model.bin").write_bytes(b"x")
    assert wm.installed_model() == "small"


def test_remove_others_keeps_only_given(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "CT2_CACHE", tmp_path)
    (tmp_path / "faster-whisper-base").mkdir(parents=True)
    (tmp_path / "faster-whisper-base" / "model.bin").write_bytes(b"b")
    (tmp_path / "faster-whisper-small").mkdir(parents=True)
    (tmp_path / "faster-whisper-small" / "model.bin").write_bytes(b"s")
    (tmp_path / "junk.txt").write_text("x", encoding="utf-8")

    wm.remove_others("small")

    assert (tmp_path / "faster-whisper-small" / "model.bin").exists()
    assert not (tmp_path / "faster-whisper-base").exists()
    assert (tmp_path / "junk.txt").exists()


def test_remove_all_deletes_all_dirs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "CT2_CACHE", tmp_path)
    (tmp_path / "faster-whisper-base").mkdir(parents=True)
    (tmp_path / "faster-whisper-base" / "model.bin").write_bytes(b"b")

    wm.remove_all()

    assert not (tmp_path / "faster-whisper-base").exists()
