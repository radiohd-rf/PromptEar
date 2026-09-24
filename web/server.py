"""Flask сервер PromptEar — API + SSE."""

import contextlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from config import LLM_ENGINE, SETTINGS_OPEN_FLAG, TEMP_DIR
from core import asr_backends as ab
from core import settings as settings_store
from core import whisper_models as wm
from core.downloader import DownloadManager
from core.events import (
    CancelledEvent,
    DoneEvent,
    DownloadDoneEvent,
    DownloadFailedEvent,
    DownloadProgressEvent,
    DraftEvent,
    EnhancingEvent,
    EnhancingStreamEvent,
    ErrorEvent,
    FileStatusEvent,
    LlmReadyEvent,
    LogEvent,
    ProgressEvent,
    ResultEvent,
    SetBusyEvent,
    SkippedEvent,
    TranscribingEvent,
)
from core.models import AudioFile, PipelineConfig
from core.pipeline import run_pipeline as run_pipeline_core
from processing import enhancer as enhancer_mod
from processing.enhancer import create_enhancer, get_llm_error
from processing.transcriber import Transcriber
from utils.extract_audio import extract_audio
from utils.files import (
    ensure_timestamps,
    find_supported_files,
    is_video_file,
    save_docx,
    save_text_output,
    strip_ts_markers,
    translated_subtitles,
)
from utils.gpu import clear_gpu_cache, detect_and_report
from utils.logger import get_logger
from web.theme import build_theme

logger = get_logger()

WEB_DIR = Path(__file__).resolve().parent
BASE_DIR = WEB_DIR.parent
UPLOAD_DIR = BASE_DIR / "output"
UPLOAD_DIR.mkdir(exist_ok=True)
INBOX_DIR = BASE_DIR / "uploads"
INBOX_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("PROMPTEAR_PORT", 5000))

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

tasks: dict[str, dict] = {}

# ── Скачивания (единый канал прогресса) ────────────────────────────────────
_download_subs: list[queue.Queue] = []
_download_manager: DownloadManager | None = None
_download_lock = threading.Lock()


def _dl_to_dict(event) -> dict | None:
    """Преобразует событие скачивания в dict для SSE /api/downloads/stream."""
    match event:
        case DownloadProgressEvent():
            return {
                "type": "download_progress",
                "download_id": event.download_id,
                "label": event.label,
                "pct": event.pct,
                "done_mb": event.done_mb,
                "total_mb": event.total_mb,
                "status": event.status,
            }
        case DownloadDoneEvent():
            return {"type": "download_done", "download_id": event.download_id}
        case DownloadFailedEvent():
            return {
                "type": "download_failed",
                "download_id": event.download_id,
                "error": event.error,
            }
    return None


def _download_emit(event) -> None:
    """Рассылает события скачивания всем подписчикам SSE + кэш сбрасывается."""
    msg = _dl_to_dict(event)
    if msg is None:
        return
    for sub in list(_download_subs):
        with contextlib.suppress(queue.Full):
            sub.put_nowait(msg)
    logger.info(f"[download] {json.dumps(msg, ensure_ascii=False)}")
    if isinstance(event, DownloadDoneEvent):
        clear_gpu_cache()  # после cuda-установки детект GPU заново


def get_download_manager() -> DownloadManager:
    global _download_manager
    with _download_lock:
        if _download_manager is None:
            _download_manager = DownloadManager(emit=_download_emit)
        return _download_manager


def _find_output_file(filename: str) -> Path | None:
    """Ищет готовый файл в output/ по имени `{stamp}-{name}`."""
    candidates = [
        p
        for p in UPLOAD_DIR.iterdir()
        if p.is_file() and (p.name == filename or p.name.endswith(f"-{filename}"))
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _event_to_dict(event) -> dict:
    """Преобразует PipelineEvent в JSON-словарь для SSE."""
    match event:
        case LogEvent():
            return {"type": "log", "message": event.message}
        case ProgressEvent():
            return {
                "type": "progress",
                "current": event.current,
                "total": event.total,
                "filename": event.filename,
                "eta": event.eta,
            }
        case TranscribingEvent():
            return {"type": "transcribing", "message": event.message}
        case DraftEvent():
            return {"type": "draft", "text": event.text, "final": event.final}
        case EnhancingEvent():
            return {
                "type": "enhancing",
                "active_pass": event.active_pass,
                "total_passes": event.total_passes,
            }
        case EnhancingStreamEvent():
            return {
                "type": "enhancing_stream",
                "filename": event.filename,
                "text": event.text,
                "active_pass": event.active_pass,
                "final": event.final,
            }
        case FileStatusEvent():
            return {
                "type": "file_status",
                "filename": event.filename,
                "status": event.status,
            }
        case SkippedEvent():
            return {"type": "skipped", "filename": event.filename, "message": event.message}
        case ResultEvent():
            return {"type": "result", "text": event.text, "filename": event.filename}
        case LlmReadyEvent():
            return {
                "type": "llm_ready",
                "llm_ok": event.llm_ok,
                "model_ok": event.model_ok,
            }
        case SetBusyEvent():
            return {"type": "busy", "busy": event.busy}
        case DoneEvent():
            return {"type": "done", "message": event.message}
        case ErrorEvent():
            return {"type": "error", "message": event.message}
        case CancelledEvent():
            return {"type": "cancelled", "message": event.message}
    return {"type": "unknown"}


def _process_files(task_id: str) -> None:
    """Запускает pipeline в отдельном потоке, отправляет события в очередь."""
    task = tasks[task_id]
    emit_queue = task["queue"]

    def emit(event):
        if isinstance(event, SkippedEvent) and task.get("skip_file") == event.filename:
            task["skip_file"] = None  # пропуск отработан — можно запрашивать следующий
        with contextlib.suppress(queue.Full):
            emit_queue.put_nowait(_event_to_dict(event))
        event_str = _event_to_dict(event)
        logger.info(f"[{task_id}] {json.dumps(event_str, ensure_ascii=False)}")

    cancel = threading.Event()
    task["cancel"] = cancel
    task["results"] = {}
    task["skip_file"] = None
    task["status"] = "processing"
    transcriber = None  # для finally ниже (Transcriber создаётся в try)

    def skip_requested(filename: str) -> bool:
        return task.get("skip_file") == filename

    try:
        files = task["files"]
        original_videos = task.get("original_videos", {})
        config = PipelineConfig(
            output_format=task.get("output_format", "docx"),
            output_dir=UPLOAD_DIR,
            multi_pass=True,
            initial_prompt=task.get("initial_prompt", "") or None,
            hotwords=task.get("hotwords", "") or None,
            llm_available=False,
            enhance_mode=task.get("enhance_mode", "auto"),
            temp_dir=TEMP_DIR / task_id,
            timestamps=bool(task.get("timestamps")),
        )
        task["enhance_mode"] = config.enhance_mode

        gpu_info = detect_and_report()
        emit(LogEvent(f"GPU: {json.dumps(gpu_info, ensure_ascii=False)}"))

        transcriber = Transcriber()
        enhancer = None
        enhancer = create_enhancer()
        llm_ok, model_ok = enhancer.is_available()
        if (
            not llm_ok
            and model_ok
            and config.enhance_mode == "auto"
            and hasattr(enhancer, "start_server")
        ):
            # Движок могли погасить после простоя — поднимаем лениво,
            # иначе auto-режим молча пропустит улучшение.
            emit(LogEvent("LLM-движок не запущен — запускаем..."))
            try:
                if enhancer.start_server():
                    llm_ok, model_ok = enhancer.is_available()
            except Exception as exc:
                emit(LogEvent(f"Не удалось запустить LLM-движок: {exc}"))
        emit(LlmReadyEvent(llm_ok=llm_ok, model_ok=model_ok))
        config.llm_available = llm_ok and model_ok
        if config.llm_available and config.enhance_mode == "auto":
            # Движок понадобится в конце задачи — запрещаем гашение простоем.
            enhancer_mod.pin_llama_server()

        audio_files = []
        display_names = task.get("display_names", {})
        for f in files:
            orig = original_videos.get(f)
            audio_files.append(
                AudioFile(
                    path=Path(f),
                    original_path=Path(orig) if orig else None,
                    temp_path=Path(f) if Path(f) in task["temp_wavs"] else None,
                    display_name=display_names.get(f, Path(f).name),
                )
            )
        emit(LogEvent(f"Добавлено {len(audio_files)} файлов"))

        run_pipeline_core(
            files=audio_files,
            config=config,
            emit=emit,
            cancel=cancel,
            transcriber=transcriber,
            enhancer=enhancer if config.llm_available else None,
            result_store=task["results"],
            skip_requested=skip_requested,
        )

        emit(DoneEvent("Готово"))

    except Exception as exc:
        logger.error(f"Pipeline error: {exc}", exc_info=True)
        emit(ErrorEvent(str(exc)))
    finally:
        with contextlib.suppress(Exception):
            enhancer_mod.unpin_llama_server()
        # Whisper-модель per-task: выгружаем детерминированно, не надеясь на GC.
        # GigaAM не трогаем — его греет сторож простоя (5 мин), unload() при
        # смене бэкенда сохранён. _loaded_alias set ⇒ грузили именно whisper.
        if transcriber is not None and transcriber._loaded_alias is not None:
            with contextlib.suppress(Exception):
                transcriber.unload()
        # загруженные исходники чистим всегда — они временные копии в uploads/
        for f_path_str in task.get("uploaded_files", []):
            p = Path(f_path_str)
            if p.exists():
                p.unlink(missing_ok=True)
        shutil.rmtree(TEMP_DIR / task_id, ignore_errors=True)
        emit_queue.put_nowait({"type": "__done__"})


@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/api/theme")
def api_theme():
    """Динамическая Material You палитра (системная тема + акцент Windows/обои).

    ?dark=1|0 — принудительно тёмная/светлая; без параметра — системная.
    """
    dark = request.args.get("dark")
    return jsonify(build_theme(None if dark is None else dark == "1"))


@app.route("/api/files", methods=["POST"])
def upload_files():
    """Принимает файлы, создаёт задачу, запускает обработку."""
    if "files" not in request.files:
        return jsonify({"error": "no files"}), 400

    active_dl = get_download_manager().active
    if active_dl and active_dl["kind"] in ("whisper", "gigaam_deps", "gigaam_model"):
        return (
            jsonify({"error": "Идёт скачивание модели — дождитесь завершения, затем запустите"}),
            409,
        )

    # Параметры запуска (модал): сохраняются в settings.json префиллом следующего раза.
    ai = request.form.get("ai")
    if ai is not None:
        enhance_mode = "auto" if ai == "1" else "none"
    else:
        enhance_mode = request.form.get("enhance_mode", "auto")

    launch_patch: dict = {
        "output_format": request.form.get("output_format", "docx"),
        "timestamps": request.form.get("timestamps") == "1",
    }
    backend = request.form.get("backend")
    if ab.is_backend(backend):
        launch_patch["asr_backend"] = backend
    if backend and ab.kind_of(backend) == "gigaam":
        from processing import gigaam

        if not gigaam.gigaam_deps_ok():
            return (
                jsonify(
                    {
                        "error": (
                            "GigaAM не установлен — выберите движок в окне "
                            "запуска и подтвердите установку компонентов"
                        )
                    }
                ),
                400,
            )
    wm_val = request.form.get("whisper_model")
    if wm_val in wm.WHISPER_CATALOG:
        launch_patch["whisper_model"] = wm_val
    use_gpu_val = request.form.get("use_gpu")
    if use_gpu_val is not None:
        launch_patch["use_gpu"] = use_gpu_val == "1"
    if ai is not None:
        launch_patch["ai_enabled"] = ai == "1"
    settings_store.save({k: v for k, v in launch_patch.items() if v is not None})

    task_id = uuid.uuid4().hex[:12]
    task_temp_dir = TEMP_DIR / task_id
    task_temp_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    saved = []
    display_names: dict[str, str] = {}
    for f in request.files.getlist("files"):
        if f.filename:
            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", f.filename)
            dest = INBOX_DIR / f"{stamp}-{safe_name}"
            f.save(str(dest))
            saved.append(str(dest))
            display_names[str(dest)] = f.filename

    if not saved:
        return jsonify({"error": "no valid files"}), 400

    # находим все поддерживаемые файлы
    all_supported = find_supported_files([Path(p) for p in saved])

    # извлекаем аудио из видео; запоминаем оригиналы для удаления после обработки
    original_videos: dict[str, str] = {}  # wav_path -> original_video_path
    temp_wavs: set[Path] = set()  # извлечённые WAV, их удалит CleanupStep/финал
    for p in list(all_supported):
        if is_video_file(p):
            try:
                wav_path = extract_audio(p, temp_dir=task_temp_dir)
                all_supported.remove(p)
                all_supported.append(wav_path)
                original_videos[str(wav_path)] = str(p)
                temp_wavs.add(wav_path)
                display_names[str(wav_path)] = display_names.get(str(p), p.name)
            except (ValueError, RuntimeError) as exc:
                app.logger.warning(f"Video extraction failed for {p.name}: {exc}")
                return jsonify({"error": str(exc)}), 400

    all_audio = [str(p) for p in all_supported]

    tasks[task_id] = {
        "queue": queue.Queue(maxsize=500),
        "files": all_audio,
        "cancel": None,
        "multi_pass": True,
        "initial_prompt": request.form.get("initial_prompt", ""),
        "hotwords": request.form.get("hotwords", ""),
        "output_format": request.form.get("output_format", "docx"),
        "enhance_mode": enhance_mode,
        "timestamps": request.form.get("timestamps") == "1",
        "output_dir": UPLOAD_DIR,
        "original_videos": original_videos,
        "display_names": display_names,
        "uploaded_files": saved,
        "temp_wavs": {str(p) for p in temp_wavs},
        "status": "processing",
        "results": {},
    }

    t = threading.Thread(target=_process_files, args=(task_id,), daemon=True)
    t.start()

    return jsonify(
        {
            "task_id": task_id,
            "file_count": len(all_audio),
            "enhance_mode": tasks[task_id]["enhance_mode"],
        }
    )


@app.route("/api/status/<task_id>")
def status_stream(task_id):
    """SSE endpoint — поток лога в реальном времени."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    def generate():
        q = task["queue"]
        while True:
            try:
                msg = q.get(timeout=30)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if msg == "__done__":
                task["status"] = "done"
                yield f"data: {json.dumps({'type': '__done__'})}\n\n"
                break
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/download/<task_id>/<filename>")
def download_file(task_id, filename):
    """Скачать готовый файл результата."""
    filepath = _find_output_file(filename)
    if filepath is None:
        return jsonify({"error": "file not found"}), 404
    return send_file(str(filepath), as_attachment=True)


@app.route("/api/cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    task = tasks.get(task_id)
    if task and task.get("cancel"):
        task["cancel"].set()
        return jsonify({"status": "cancelled"})
    return jsonify({"error": "task not found"}), 404


@app.route("/api/skip/<task_id>/<filename>", methods=["POST"])
def skip_file(task_id, filename):
    """Пропускает текущий файл: прерывает его обработку, переходит к следующему."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    if task.get("skip_file"):
        return jsonify({"error": "skip already requested"}), 409
    task["skip_file"] = filename
    return jsonify({"status": "skipping", "filename": filename})


@app.route("/api/results/<task_id>")
def task_results(task_id):
    """Возвращает сохранённые результаты (имена файлов и тексты) для задачи."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    results = []
    for result in task.get("results", {}).values():
        results.append(
            {
                "filename": result.audio.display_name or result.audio.path.name,
                "text": result.text,
            }
        )
    return jsonify({"results": results})


def _ensure_llm(enhancer) -> tuple[bool, str | None]:
    """Гарантирует, что ИИ-движок доступен (llama-server запущен, модель на месте).

    Модель может быть скачана, но llama-server не поднят (например, после
    перезапуска приложения) — тогда стартуем его лениво, по первому запросу
    улучшения/перевода, а не предлагаем скачивать модель заново.
    """
    from processing.enhancer import get_llm_error

    llm_ok, model_ok = enhancer.is_available()
    if llm_ok and model_ok:
        return True, None
    if not model_ok:
        return False, "Модель ИИ не установлена"
    ok = enhancer.start_server()
    if ok:
        llm_ok2, model_ok2 = enhancer.is_available()
        if llm_ok2 and model_ok2:
            return True, None
    return False, get_llm_error() or "LLM недоступен"


@app.route("/api/enhance/<task_id>/<filename>", methods=["POST"])
def enhance_file(task_id, filename):
    """Улучшает уже сохранённый черновик (режим ask) и перезаписывает файл."""
    from threading import Event

    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    result = next(
        (
            r
            for r in task.get("results", {}).values()
            if (r.audio.display_name or r.audio.path.name) == filename
        ),
        None,
    )
    if result is None:
        return jsonify({"error": "file result not found"}), 404

    try:
        enhancer = create_enhancer()
        ready, err = _ensure_llm(enhancer)
        if not ready:
            return jsonify({"error": err or "LLM недоступен"}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409

    cancel = Event()
    elapsed_passes = {"n": 0}
    task_queue = task.get("queue")

    def mp_progress(msg: str) -> None:
        import re

        m = re.match(r"проход (\d)/(\d)", msg, re.IGNORECASE)
        if m:
            elapsed_passes["n"] += 1
            ev = {
                "type": "enhancing",
                "active_pass": int(m.group(1)),
                "total_passes": int(m.group(2)),
            }
            with contextlib.suppress(queue.Full):
                if task_queue is not None:
                    task_queue.put_nowait(ev)
            logger.info(f"[{task_id}] {json.dumps(ev, ensure_ascii=False)}")

    def mp_stream(text_so_far: str, _pass: int = 0) -> None:
        ev = {
            "type": "enhancing_stream",
            "filename": filename,
            "text": text_so_far,
            "active_pass": _pass,
            "final": False,
        }
        with contextlib.suppress(queue.Full):
            if task_queue is not None:
                task_queue.put_nowait(ev)
        logger.info(f"[{task_id}] {json.dumps(ev, ensure_ascii=False)}")

    original = result.text
    _ecancel_key = f"{task_id}\0{filename}"
    with _enhance_cancels_lock:
        _enhance_cancels[_ecancel_key] = cancel
    try:
        use_ts = bool(task.get("timestamps")) and result.segments is not None
        # Если включены таймкоды — подаём модели размеченный текст,
        # чтобы Gemma сама держала / переносила метки вместе с фрагментами.
        enhance_input = original
        if use_ts:
            enhance_input = ensure_timestamps(result.segments, original)
        new_text = enhancer.enhance_multi_pass(
            enhance_input,
            task.get("initial_prompt", "") or "",
            progress_callback=mp_progress,
            cancel=cancel,
            stream_callback=mp_stream,
            timestamps=use_ts,
        )
        if cancel.is_set():
            # пользователь остановил улучшение — не трогаем result.text и файл
            return jsonify({"error": "Улучшение отменено"}), 499
        if use_ts:
            new_text = ensure_timestamps(result.segments, new_text)
        result.text = new_text
    except Exception as exc:
        return jsonify({"error": f"Ошибка улучшения: {exc}"}), 500
    finally:
        with _enhance_cancels_lock:
            _enhance_cancels.pop(_ecancel_key, None)

    filepath = result.audio.original_path or result.audio.path
    out_dir = task.get("output_dir") or filepath.parent
    out_path = Path(out_dir) / f"{filepath.stem}.{task.get('output_format', 'docx')}"
    text_to_save = result.text
    if (
        task.get("timestamps")
        and task.get("output_format", "docx").lower() not in ("srt", "vtt")
        and result.segments is not None
    ):
        text_to_save = ensure_timestamps(result.segments, result.text)
    if out_path.suffix.lower() == ".docx":
        save_docx(out_path, text_to_save)
    else:
        save_text_output(
            out_path,
            task.get("output_format", "docx"),
            text_to_save,
            segments=result.segments,
            duration=result.duration_sec,
        )
    result.output_path = out_path
    return jsonify({"text": text_to_save, "output_path": str(out_path)})


@app.route("/api/translate/<task_id>/<filename>", methods=["POST"])
def translate_file(task_id, filename):
    """Переводит сохранённый результат на указанный язык и пишет отдельный файл.

    Тело запроса: {"language_code": "en", "language_name": "Английский"}.
    Файл результата сохраняется как `{stem}-{language_code}.{output_format}`
    (для srt/vtt перевод раскладывается по сегментам с таймкодами).
    """
    from threading import Event

    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    result = next(
        (
            r
            for r in task.get("results", {}).values()
            if (r.audio.display_name or r.audio.path.name) == filename
        ),
        None,
    )
    if result is None:
        return jsonify({"error": "file result not found"}), 404

    data = request.get_json(silent=True) or {}
    language_code = (data.get("language_code") or "").strip().lower()
    language_name = (data.get("language_name") or "").strip()
    if not language_code:
        return jsonify({"error": "language_code не указан"}), 400

    try:
        enhancer = create_enhancer()
        ready, err = _ensure_llm(enhancer)
        if not ready:
            return jsonify({"error": err or "LLM недоступен"}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409

    cancel = Event()
    _cancel_key = f"{task_id}\0{filename}"
    with _translate_cancels_lock:
        _translate_cancels[_cancel_key] = cancel

    try:
        # Переводим исходный текст без (уже устаревших) таймкод-меток Whisper:
        # перевод должен быть чистым, метки всё равно не соответствуют его длине.
        source_text = strip_ts_markers(result.text)
        try:
            translated = enhancer.translate(
                source_text,
                language_code,
                language_name=language_name,
                cancel=cancel,
            )
        except NotImplementedError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"Ошибка перевода: {exc}"}), 500
    finally:
        with _translate_cancels_lock:
            _translate_cancels.pop(_cancel_key, None)

    if cancel.is_set():
        return jsonify({"error": "Перевод отменён"}), 499

    if not translated or not translated.strip():
        return jsonify({"error": "Пустой результат перевода"}), 500

    filepath = result.audio.original_path or result.audio.path
    out_dir = task.get("output_dir") or filepath.parent
    output_format = task.get("output_format", "docx")
    # имя без расширения исходника → {stem}-{lang}.{fmt}
    out_path = out_dir / f"{filepath.stem}-{language_code}.{output_format}"

    if output_format.lower() in ("srt", "vtt"):
        if result.segments:
            text_to_save = translated_subtitles(
                result.segments, translated, output_format
            )
            out_path.write_text(text_to_save, encoding="utf-8")
        else:
            # нет сегментов — сохраняем как один субтитр-блок на весь текст
            save_text_output(
                out_path,
                output_format,
                translated,
                segments=result.segments,
                duration=result.duration_sec,
            )
    elif out_path.suffix.lower() == ".docx":
        save_docx(out_path, translated)
    else:
        save_text_output(out_path, output_format, translated)
    task.setdefault("translated_paths", {})[filename] = str(out_path)
    return jsonify(
        {
            "text": translated,
            "output_path": str(out_path),
            "language_name": language_name or language_code,
        }
    )


@app.route("/api/open-doc/<task_id>/<filename>", methods=["POST"])
def open_doc(task_id, filename):
    """Открывает готовый документ файла во внешнем редакторе (os.startfile).

    Тело: {"translated": true} — открыть переведённую копию, иначе основной результат.
    """
    import os

    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "задача не найдена"}), 404

    data = request.get_json(silent=True) or {}
    path = None
    if data.get("translated"):
        path = task.get("translated_paths", {}).get(filename)
    if path is None:
        result = next(
            (
                r
                for r in task.get("results", {}).values()
                if (r.audio.display_name or r.audio.path.name) == filename
            ),
            None,
        )
        if result is None:
            return jsonify({"error": "файл не найден"}), 404
        path = result.output_path
    if not path:
        return jsonify({"error": "готовый документ ещё не сохранён"}), 404

    p = Path(path)
    if not p.exists():
        return jsonify({"error": f"файл не найден: {p.name}"}), 404
    try:
        os.startfile(str(p))  # Windows: открывает в редакторе по умолчанию
    except OSError as exc:
        return jsonify({"error": f"не удалось открыть: {exc}"}), 500
    return jsonify({"ok": True, "name": p.name})


@app.route("/api/translate/cancel/<task_id>/<filename>", methods=["POST"])
def translate_cancel(task_id, filename):
    """Прерывает идущий перевод файла (если такой есть)."""
    with _translate_cancels_lock:
        ev = _translate_cancels.get(f"{task_id}\0{filename}")
    if ev is not None:
        ev.set()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "перевод не идёт"})

@app.route("/api/enhance/cancel/<task_id>/<filename>", methods=["POST"])
def enhance_cancel(task_id, filename):
    """Прерывает идущее улучшение файла (если такое есть)."""
    with _enhance_cancels_lock:
        ev = _enhance_cancels.get(f"{task_id}\0{filename}")
    if ev is not None:
        ev.set()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "улучшение не идёт"})


@app.route("/api/gpu")
def gpu_status():
    """Текущее состояние GPU (nvidia-smi + ctranslate2, без раннего импорта torch)."""
    return jsonify(detect_and_report())


@app.route("/api/settings", methods=["GET"])
def get_settings():
    data = settings_store.load()
    # Показываем фактический порт работающего движка, а не устаревшее значение
    # из settings.json (например, если порт выбрали вручную или авто-подбором).
    actual = enhancer_mod._llama_actual_port
    if actual is not None and data.get("llm_port") != actual:
        data["llm_port"] = actual
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def post_settings():
    """Сохраняет патч настроек в settings.json (валидные ключи)."""
    patch = request.get_json(silent=True) or {}
    was_gpu = bool(settings_store.load().get("use_gpu", False))
    result = settings_store.save(patch)

    # Включили «Использовать GPU» → убеждаемся, что под ИИ стоит CUDA-сборка
    # llama.cpp (CPU-сборка из комплекта GPU не понимает). Качаем один раз в фоне.
    if (
        bool(patch.get("use_gpu"))
        and not was_gpu
        and not _has_cuda_llama_build()
    ):
        threading.Thread(target=_auto_cuda_build, daemon=True).start()

    # Любое переключение «Использовать GPU» останавливает llama-server:
    #  - выключили GPU → модель (запущенная с -ngl) освобождает видеопамять;
    #  - включили GPU → старый CPU-сервер (-ngl 0) не переиспользуется и при
    #    следующем запуске движок стартует заново уже с GPU-оффлоадом (-ngl 99).
    if "use_gpu" in patch and bool(patch["use_gpu"]) != was_gpu:
        with contextlib.suppress(Exception):
            enhancer_mod.stop_llama_server()
        with contextlib.suppress(Exception):
            enhancer_mod._llama_actual_port = None

    return jsonify(result)


def _has_cuda_llama_build() -> bool:
    from config import LLAMA_DIR

    return any(LLAMA_DIR.rglob("ggml-cuda*.dll"))


def _auto_cuda_build() -> None:
    """Фоновая установка CUDA-сборки llama.cpp после включения «Использовать GPU»."""
    import time

    try:
        # даём POST /api/settings спокойно вернуться клиенту
        time.sleep(0.5)
        if _has_cuda_llama_build():
            return
        manager = get_download_manager()
        if manager.active is not None:
            return
        manager.start("llama_gpu")
    except Exception as exc:
        logger.error(f"[llm] автоматическая установка CUDA-сборки не удалась: {exc}")


@app.route("/api/models")
def models_catalog():
    """Каталог движков распознавания + установленные + статус Gemma/GigaAM."""
    from config import LLM_MODEL_PATH
    from processing import gigaam as gigaam_engine

    backends = []
    for entry in ab.get_backends():
        entry = dict(entry)
        entry["installed"] = ab.is_installed(entry["id"])
        backends.append(entry)
    current = settings_store.load().get("asr_backend")
    if not (isinstance(current, str) and ab.is_backend(current)):
        current = ab.installed_backend() or ab.DEFAULT_BACKEND
    return jsonify(
        {
            "backends": backends,
            "current": current,
            "installed": ab.installed_backend(),
            "gigaam_deps_ok": gigaam_engine.gigaam_deps_ok(),
            "gemma_installed": LLM_MODEL_PATH.exists(),
        }
    )


@app.route("/api/downloads", methods=["POST"])
def start_download():
    """Запускает скачивание: whisper|gigaam_deps|gigaam_model|gemma|llama|llama_gpu."""
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in (
        "whisper",
        "gigaam_deps",
        "gigaam_model",
        "gemma",
        "llama",
        "llama_gpu",
    ):
        return jsonify({"error": "Неизвестный тип скачивания"}), 400
    model = data.get("model")
    if kind == "whisper":
        if model and not (ab.is_backend(model) and ab.kind_of(model) == "whisper"):
            return jsonify({"error": f"Неизвестная модель: {model}"}), 400
        if not model:
            model = ab.DEFAULT_BACKEND
    elif kind == "gigaam_model":
        if not (model and ab.is_backend(model) and ab.kind_of(model) == "gigaam"):
            return jsonify({"error": f"Неизвестный вариант GigaAM: {model}"}), 400
    manager = get_download_manager()
    if manager.active is not None:
        return jsonify(
            {
                "error": "Уже идёт скачивание",
                "status": manager.status(),
            }
        ), 409
    try:
        download_id = manager.start(kind, model)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"download_id": download_id})


@app.route("/api/downloads/status")
def downloads_status():
    """Состояние текущего скачивания (active: {id, kind, model, label} | None)."""
    return jsonify(get_download_manager().status())


@app.route("/api/downloads/stream")
def downloads_stream():
    """Глобальный SSE-канал прогресса скачиваний."""
    q: queue.Queue = queue.Queue(maxsize=500)
    _download_subs.append(q)
    active = get_download_manager().active

    def generate():
        try:
            if active:
                yield f"data: {json.dumps({'type': 'download_active', 'status': active})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            with contextlib.suppress(ValueError):
                _download_subs.remove(q)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/startup")
def startup_intent():
    """Единоразовые указания с прошлого запуска (снимаются после чтения).

    Сейчас отвечает {"open_settings": bool} — открыть ли настройки при старте.
    """
    want = SETTINGS_OPEN_FLAG.exists()
    if want:
        with contextlib.suppress(OSError):
            SETTINGS_OPEN_FLAG.unlink()
    return jsonify({"open_settings": want})


@app.route("/api/restart", methods=["POST"])
def restart_app():
    """Перезапускает приложение.

    Тело {"open_settings": true} — после перезапуска открыть окно настроек
    (полезно при смене GPU, когда нужен рестарт).
    """
    data = request.get_json(silent=True) or {}
    if data.get("open_settings"):
        try:
            SETTINGS_OPEN_FLAG.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_OPEN_FLAG.write_text("1", encoding="utf-8")
        except OSError:
            pass
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")],
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return jsonify({"error": f"Не удалось перезапустить: {exc}"}), 500
    threading.Timer(1.0, lambda: os._exit(0)).start()  # noqa: PLR04 — плановый рестарт
    return jsonify({"ok": True})


_llm_install_running = False
_llm_install_last_error = ""
_llm_install_lock = threading.Lock()
_llm_install_cancel = threading.Event()
_translate_cancels: dict[str, threading.Event] = {}
_translate_cancels_lock = threading.Lock()
_enhance_cancels: dict[str, threading.Event] = {}
_enhance_cancels_lock = threading.Lock()


@app.route("/api/llm")
def llm_check():
    try:
        enhancer = create_enhancer()
        llm_ok, model_ok = enhancer.is_available()
        error = None
        if not llm_ok:
            error = getattr(enhancer, "last_error", None) or get_llm_error()
            # Причина ещё не установилась, но модель на месте — движок просто
            # не запущен и стартует лениво по первому улучшению/переводу.
            if not error and model_ok:
                error = (
                    "Движок не запущен — стартует автоматически "
                    "при первом «Улучшить с ИИ» или «Перевести»"
                )
        return jsonify(
            {
                "llm_ok": llm_ok,
                "model_ok": model_ok,
                "engine": LLM_ENGINE,
                "port": enhancer_mod._llama_actual_port or None,
                "error": error,
                "installing": _llm_install_running,
                "install_error": _llm_install_last_error,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "llm_ok": False,
                "model_ok": False,
                "engine": LLM_ENGINE,
                "port": enhancer_mod._llama_actual_port or None,
                "error": str(exc),
                "installing": _llm_install_running,
                "install_error": _llm_install_last_error,
            }
        )


@app.route("/api/llm/download", methods=["POST"])
def llm_download():
    """Скачивает GGUF-модель ИИ в фоне и поднимает llama-server."""
    global _llm_install_running, _llm_install_last_error
    if _llm_install_running:
        return jsonify({"installing": True})
    with _llm_install_lock:
        if _llm_install_running:
            return jsonify({"installing": True})
        _llm_install_running = True
        _llm_install_last_error = ""
        _llm_install_cancel.clear()

    def worker():
        global _llm_install_running, _llm_install_last_error
        try:
            from core.installers import GemmaInstaller

            GemmaInstaller.download_and_start(cancel=_llm_install_cancel)
        except Exception as exc:
            if not _llm_install_cancel.is_set():
                _llm_install_last_error = str(exc)
        finally:
            _llm_install_running = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"installing": True})


@app.route("/api/llm/cancel", methods=["POST"])
def llm_cancel():
    """Прерывает фоновое скачивание GGUF-модели ИИ."""
    _llm_install_cancel.set()
    return jsonify({"ok": True})


@app.route("/api/llm/delete", methods=["POST"])
def llm_delete():
    """Удаляет модель ИИ (GGUF): останавливает llama-server (он держит файл) и стирает модель."""
    import subprocess
    import time

    from config import LLM_MODEL_PATH

    if _llm_install_running:
        return jsonify({"ok": False, "error": "Скачивание модели ещё идёт"}), 409
    if not LLM_MODEL_PATH.exists():
        return jsonify({"ok": True})

    # llama-server держит GGUF открытым — Windows не даст удалить, пока процесс жив.
    subprocess.run(
        ["taskkill", "/IM", "llama-server.exe", "/F"],
        capture_output=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Файл может быть ещё занят пару мгновений после taskkill — пробуем с ретраями.
    last_exc: Exception | None = None
    for _ in range(10):
        try:
            LLM_MODEL_PATH.unlink(missing_ok=True)
            break
        except OSError as exc:
            last_exc = exc
            time.sleep(0.3)
    if LLM_MODEL_PATH.exists():
        return jsonify({"ok": False, "error": f"Не удалось удалить файл: {last_exc}"}), 500

    settings_store.save({"ai_enabled": False})
    return jsonify({"ok": True})


def _port_has_llama(port: int) -> bool:
    """Порт занят именно нашим llama-server (отвечает на /health 200)?"""
    import requests

    try:
        return (
            requests.get(f"http://127.0.0.1:{port}/health", timeout=0.4).status_code
            == 200
        )
    except requests.RequestException:
        return False


@app.route("/api/llm/port", methods=["POST"])
def llm_set_port():
    """Задаёт порт llama-server вручную и пытается перезапустить движок."""
    data = request.get_json(silent=True) or {}
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Некорректный порт"}), 400
    if not (1024 <= port <= 65535):
        return jsonify({"ok": False, "error": "Порт должен быть в диапазоне 1024–65535"}), 400

    # Порт уже занят: если это наш рабочий llama-server — это успех, а не ошибка
    # (иначе пользователь вводит работающий порт и получает «занят другим процессом»).
    port_busy = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", port))
        sock.close()
    except OSError:
        port_busy = True

    except_our_port = False
    if port_busy and _port_has_llama(port):
        enhancer = create_enhancer()
        # Порт занят нашим сервером, но в правильном ли он режиме? При включённом
        # GPU и живом CPU-движке (-ngl 0) is_available() вернёт False — тогда
        # идём к start_server() ниже, который убьёт CPU-движок и поднимет с GPU.
        if enhancer.is_available()[0]:
            if enhancer_mod._llama_actual_port != port:
                enhancer_mod._llama_actual_port = port
            settings_store.save({"llm_port": port})
            return jsonify({"ok": True, "port": port, "engine": LLM_ENGINE})
        except_our_port = True

    if port_busy and not except_our_port:
        return jsonify({"ok": False, "error": f"Порт {port} уже занят другим процессом"}), 409

    try:
        enhancer = create_enhancer()
        if not hasattr(enhancer, "start_server"):
            return jsonify({"ok": False, "error": "Текущий LLM-движок не умеет менять порт"}), 400
        if hasattr(enhancer, "port_override"):
            enhancer.port_override = port
        ok = enhancer.start_server(port=port)
        # Сообщаем ФАКТИЧЕСКИЙ порт (движок мог быть уже запущен на другом).
        actual = enhancer_mod._llama_actual_port or port
        params = {"ok": ok, "port": actual, "engine": LLM_ENGINE}
        if ok:
            settings_store.save({"llm_port": actual})
        if not ok:
            params["error"] = getattr(enhancer, "last_error", None)
        return jsonify(params)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/open-output")
def open_output():
    import subprocess

    select = request.args.get("select")
    if select:
        target = UPLOAD_DIR / select
        # защита от path traversal
        try:
            target.resolve().relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return jsonify({"error": "invalid path"}), 400
        if target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
            return jsonify({"ok": True})
    subprocess.Popen(["explorer", str(UPLOAD_DIR)])
    return jsonify({"ok": True})
