"""Тесты загрузчика моделей (core/downloader.py) с requests-mock (без сети)."""

import threading

import pytest
import requests
import requests_mock

from core import downloader as dl
from core import whisper_models as wm
from core.downloader import DownloadManager


def test_fetch_file_writes_dest(tmp_path) -> None:
    payload = b"0123456789" * 1000
    dest = tmp_path / "lib/model.bin"
    with requests_mock.Mocker() as m:
        m.get("https://example.test/model.bin", content=payload)
        dl.fetch_file("https://example.test/model.bin", dest)
    assert dest.read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))


def test_fetch_file_reports_progress(tmp_path) -> None:
    payload = b"a" * 100_000
    dest = tmp_path / "m.bin"
    seen: list[tuple[int, int | None]] = []
    with requests_mock.Mocker() as m:
        m.get(
            "https://example.test/m.bin",
            content=payload,
            headers={"Content-Length": str(len(payload))},
        )
        dl.fetch_file(
            "https://example.test/m.bin",
            dest,
            on_progress=lambda done, total: seen.append((done, total)),
        )
    assert seen
    assert seen[-1][0] == len(payload)
    assert seen[-1][1] == len(payload)


def test_fetch_file_cancel_removes_part(tmp_path) -> None:
    payload = b"b" * 1_000_000
    cancel = threading.Event()
    dest = tmp_path / "m.bin"
    # отменяем после самого первого чанка
    with requests_mock.Mocker() as m:
        m.get("https://example.test/m.bin", content=payload)
        with pytest.raises(RuntimeError, match="Скачивание отменено"):
            dl.fetch_file(
                "https://example.test/m.bin",
                dest,
                cancel=cancel,
                on_progress=lambda done, total: cancel.set(),
            )
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_fetch_file_http_error_removes_part(tmp_path) -> None:
    dest = tmp_path / "m.bin"
    with requests_mock.Mocker() as m:
        m.get("https://example.test/m.bin", status_code=404)
        with pytest.raises(requests.HTTPError):
            dl.fetch_file("https://example.test/m.bin", dest)
    assert not dest.exists()


def test_list_hf_files_filters() -> None:
    api = dl._hf_api("Systran/faster-whisper-base")
    siblings = [
        {"rfilename": "model.bin"},
        {"rfilename": "config.json"},
        {"rfilename": "tokenizer.json"},
        {"rfilename": "vocabulary.txt"},
        {"rfilename": "README.md"},
        {"rfilename": "flax_model.msgpack"},
    ]
    with requests_mock.Mocker() as m:
        m.get(api, json={"siblings": siblings})
        names = dl.list_hf_files("Systran/faster-whisper-base")
    assert names == ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]


def test_download_whisper_model(tmp_path) -> None:
    repo = wm.resolve("base")
    dest = tmp_path / "faster-whisper-base"
    files = ["config.json", "model.bin"]
    progress: list[str] = []
    with requests_mock.Mocker() as m:
        m.get(
            dl._hf_api(repo),
            json={"siblings": [{"rfilename": f} for f in files]},
        )
        for f in files:
            m.get(
                dl._hf_file_url(repo, f),
                content=(f"content-of-{f}").encode(),
            )
        dl.download_whisper_model(
            "base",
            dest,
            on_progress=lambda label, done, total: progress.append(label),
        )
    assert (dest / "model.bin").read_bytes() == b"content-of-model.bin"
    assert (dest / "config.json").exists()
    assert progress[-1] == "base: готово"


def test_download_whisper_model_missing_model_bin(tmp_path) -> None:
    repo = wm.resolve("base")
    dest = tmp_path / "m"
    with requests_mock.Mocker() as m:
        m.get(dl._hf_api(repo), json={"siblings": [{"rfilename": "config.json"}]})
        with pytest.raises(RuntimeError, match="model.bin"):
            dl.download_whisper_model("base", dest)


def test_download_manager_rejects_parallel(monkeypatch) -> None:
    events: list = []
    manager = DownloadManager(emit=events.append)
    monkeypatch.setattr(manager, "_run", lambda _kind, _model, _id: None)
    first = manager.start("gemma", "gemma-4-E2B-it")
    assert manager.active is not None
    assert manager.active["kind"] == "gemma"
    assert manager.active["id"] == first
    with pytest.raises(RuntimeError, match="Уже идёт другое скачивание"):
        manager.start("llama")


def test_download_manager_status_shape(monkeypatch) -> None:
    events: list = []
    manager = DownloadManager(emit=events.append)
    assert manager.status() == {"active": None}
    monkeypatch.setattr(manager, "_run", lambda _kind, _model, _id: None)
    manager.start("llama_gpu", None)
    status = manager.status()
    assert status["active"] is not None
    assert status["active"]["label"] == "llama.cpp: CUDA-сборка (~500 МБ)"
