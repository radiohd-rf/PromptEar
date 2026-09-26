"""Тесты настроек (core/settings.py)."""

import json

import core.settings as settings


def _patch_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")


def test_load_defaults_when_no_file(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    data = settings.load()
    assert data["whisper_model"] == "base"
    assert data["asr_backend"] == "whisper_base"
    assert data["use_gpu"] is False
    assert data["output_format"] == "docx"
    assert data["timestamps"] is False
    assert data["ai_enabled"] is False
    assert data["llm_port"] == 8080


def test_load_reads_saved_values(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"whisper_model": "small", "use_gpu": True}), encoding="utf-8"
    )
    data = settings.load()
    assert data["whisper_model"] == "small"
    assert data["use_gpu"] is True
    assert data["output_format"] == "docx"


def test_load_ignores_unknown_keys(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"whisper_model": "base", "unknown_key": 42}), encoding="utf-8"
    )
    assert "unknown_key" not in settings.load()


def test_load_tolerates_broken_json(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.load()["llm_port"] == 8080


def test_save_persists_patch(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    result = settings.save({"whisper_model": "large-v3"})
    assert result["whisper_model"] == "large-v3"
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["whisper_model"] == "large-v3"
    assert on_disk["output_format"] == "docx"


def test_save_ignores_unknown_patch_keys(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    result = settings.save({"nope": 1})
    assert "nope" not in result


def test_save_tolerates_unwritable_dir(monkeypatch, tmp_path) -> None:
    _patch_path(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "no_such_dir" / "settings.json")
    result = settings.save({"whisper_model": "base"})
    assert result["whisper_model"] == "base"
