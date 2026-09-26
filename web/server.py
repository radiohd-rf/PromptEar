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

from config import AUDIO_CACHE_DIR, LLM_ENGINE, SETTINGS_OPEN_FLAG, TEMP_DIR
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
from processing.enhancer import create_enhancer, get_llm_error, llama_port_available
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
            return {
                "type": "draft",
                "text": event.text,
                "final": event.final,
                "filename": event.filename,
            }
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
                "audio_ok": event.audio_ok,
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
        need_llm = config.enhance_mode == "auto" or bool(task.get("translate_lang"))
        if not llm_ok and model_ok and need_llm and hasattr(enhancer, "start_server"):
            # Движок могли погасить после простоя — поднимаем лениво,
            # иначе auto-режим/перевод молча пропустит улучшение/перевод.
            emit(LogEvent("LLM-движок не запущен — запускаем..."))
            try:
                if enhancer.start_server():
                    llm_ok, model_ok = enhancer.is_available()
            except Exception as exc:
                emit(LogEvent(f"Не удалось запустить LLM-движок: {exc}"))
        emit(LlmReadyEvent(llm_ok=llm_ok, model_ok=model_ok))
        config.llm_available = llm_ok and model_ok
        if config.llm_available and need_llm:
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
            audio_cache_dir=AUDIO_CACHE_DIR / task_id,
            audio_cache_map=task["audio_cache"],
        )

        # Разовый перевод результата (модал запуска): транскрибация →
        # (улучшение в auto) → перевод. Перевод перезаписывает единственный
        # файл результата — в папке остаётся один документ на источник.
        tr_lang = task.get("translate_lang") or ""
        if tr_lang:
            if not config.llm_available:
                emit(LogEvent("⚠ Перевод отменён: LLM-движок недоступен"))
            else:
                _translate_pipeline_results(task_id, task, enhancer, emit, cancel, tr_lang)
            task["translate_lang"] = ""  # на молчаливый повтор не влияет

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


def _translate_pipeline_results(
    task_id: str,
    task: dict,
    enhancer,
    emit,
    cancel,
    lang_code: str,
) -> None:
    """Переводит каждый готовый результат задачи (модал запуска «Перевести»).

    Перевод перезаписывает ЕДИНСТВЕННЫЙ файл результата (result.output_path) —
    исходя из параметров запуска в папке остаётся один документ на источник.
    Шлёт SSE-событие result с полем lang; state текста тоже обновляется,
    чтобы /api/results возвращал перевод, а не исходный черновик.
    """
    emit_queue = task["queue"]
    lang_name = task.get("translate_name") or lang_code
    use_ts = bool(task.get("timestamps"))
    output_format = task.get("output_format", "docx")

    for _key, result in list(task.get("results", {}).items()):
        display = result.audio.display_name or result.audio.path.name
        source = strip_ts_markers(result.text)
        if not source or not source.strip():
            emit(LogEvent(f"⏭ Нет текста для перевода: {display}"))
            continue
        emit(LogEvent(f"🌐 Перевод: {display} → {lang_name}..."))
        try:
            translated = enhancer.translate(
                source,
                lang_code,
                language_name=lang_name,
                cancel=cancel,
            )
        except Exception as exc:
            emit(LogEvent(f"⚠ Не удалось перевести {display}: {exc}"))
            continue
        if cancel.is_set():
            break
        if not translated or not translated.strip():
            emit(LogEvent(f"⚠ Пустой результат перевода: {display}"))
            continue
        if use_ts and result.segments is not None:
            translated = ensure_timestamps(result.segments, translated)

        out_path = result.output_path or (
            (result.audio.original_path or result.audio.path).with_suffix(f".{output_format}")
        )
        if output_format.lower() in ("srt", "vtt") and result.segments is not None:
            out_path.write_text(
                translated_subtitles(result.segments, translated, output_format),
                encoding="utf-8",
            )
        elif output_format.lower() == "docx":
            save_docx(out_path, translated)
        else:
            save_text_output(
                out_path,
                output_format,
                translated,
                segments=result.segments,
                duration=result.duration_sec,
            )
        result.text = translated
        task.setdefault("refined_paths", {})[display] = str(out_path)
        emit(LogEvent(f"✅ Перевод сохранён: {out_path.name}"))
        ev = {
            "type": "result",
            "filename": display,
            "text": translated,
            "lang": lang_code,
        }
        with contextlib.suppress(queue.Full):
            emit_queue.put_nowait(ev)
        logger.info(f"[{task_id}] {json.dumps(ev, ensure_ascii=False)}")


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


def _sweep_audio_cache(keep_task_id: str | None = None) -> None:
    """Выметает сессионный кэш аудио кроме задачи keep_task_id.

    Вызывается при новом запуске (чужие задачи) и на старте приложения
    (задачи живут в памяти процесса — после перезапуска пути не актуальны).
    """
    with contextlib.suppress(OSError):
        for entry in AUDIO_CACHE_DIR.iterdir():
            if entry.is_dir() and entry.name != keep_task_id:
                shutil.rmtree(entry, ignore_errors=True)


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
    # Разовый перевод результата (в pipeline: транскрибация → (улучшение) → перевод).
    translate_on = request.form.get("translate") == "1"
    translate_lang = (request.form.get("translate_lang") or "").strip().lower()
    translate_name = (request.form.get("translate_name") or "").strip()
    if translate_on and not translate_lang:
        translate_on = False
    settings_store.save({k: v for k, v in launch_patch.items() if v is not None})

    task_id = uuid.uuid4().hex[:12]
    _sweep_audio_cache(keep_task_id=task_id)
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
        "translate_lang": translate_lang if translate_on else "",
        "translate_name": translate_name if translate_on else "",
        "output_dir": UPLOAD_DIR,
        "original_videos": original_videos,
        "display_names": display_names,
        "uploaded_files": saved,
        "temp_wavs": {str(p) for p in temp_wavs},
        "status": "processing",
        "results": {},
        "audio_cache": {},
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


def _audio_file(task_id: str, filename: str) -> Path | None:
    """Путь к аудио из сессионного кэша плеера (или None)."""
    task = tasks.get(task_id)
    if not task:
        return None
    path = task.get("audio_cache", {}).get(filename)
    if path is None or not path.exists():
        return None
    return path


@app.route("/api/audio/<task_id>/<path:filename>")
def audio_file(task_id, filename):
    """Раздача WAV плееру с поддержкой HTTP Range.

    HTML5 `<audio>` требует 206 + Content-Range для перемотки и докачки,
    иначе вебвью тянет файл целиком и seek не работает.
    """
    path = _audio_file(task_id, filename)
    if path is None:
        return jsonify({"error": "audio not found"}), 404
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}
    range_hdr = request.headers.get("Range")
    if range_hdr:
        m = re.match(r"bytes=(\d*)-(\d*)", range_hdr.strip())
        if m:
            start_s, end_s = m.group(1), m.group(2)
            try:
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
            except ValueError:
                start, end = 0, size - 1
            if start >= size or start > end:
                headers["Content-Range"] = f"bytes */{size}"
                return Response(status=416, headers=headers)
            end = min(end, size - 1)

            def gen():
                remaining = end - start + 1
                with open(str(path), "rb") as fh:
                    fh.seek(start)
                    while remaining > 0:
                        chunk = fh.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
            return Response(gen(), status=206, headers=headers)
    return send_file(str(path), mimetype="audio/wav", conditional=True)


@app.route("/api/audio/remove/<task_id>/<path:filename>", methods=["POST"])
def audio_remove(task_id, filename):
    """Удаляет аудио текущего файла из сессионного кэша (строка удалена
    из окна «Файлы»)."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    entry = task.get("audio_cache", {}).pop(filename, None)
    if entry is not None:
        with contextlib.suppress(OSError):
            entry.unlink(missing_ok=True)
    return jsonify({"status": "removed"})


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
    with_ts = bool(task.get("timestamps"))
    audio_map = task.get("audio_cache", {})
    for result in task.get("results", {}).values():
        text = result.text
        # Окно должно показывать тот же текст, что записан в файл:
        # если включены таймкоды — восстанавливаем метки [MM:SS] на абзацах,
        # иначе после завершения метки исчезали из документа.
        if with_ts and text.strip() and result.segments:
            text = ensure_timestamps(result.segments, text)
        filename = result.audio.display_name or result.audio.path.name
        results.append(
            {
                "filename": filename,
                "text": text,
                "audio_ok": filename in audio_map,
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


@app.route("/api/open-doc/<task_id>/<filename>", methods=["POST"])
def open_doc(task_id, filename):
    """Открывает готовый документ файла во внешнем редакторе (os.startfile).

    Сначала ищет результат улучшения/перевода (refined_paths), иначе — основной.
    """
    import os

    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "задача не найдена"}), 404

    path = task.get("refined_paths", {}).get(filename)
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
    if bool(patch.get("use_gpu")) and not was_gpu and not _has_cuda_llama_build():
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
    gigaam_ready = gigaam_engine.gigaam_deps_ok()

    def _usable(bid: str) -> bool:
        """Рабочий ли бэкенд: whisper — модель на месте, gigaam — ещё и deps."""
        if not ab.is_backend(bid):
            return False
        if not ab.is_installed(bid):
            return False
        return ab.kind_of(bid) != "gigaam" or gigaam_ready

    current = settings_store.load().get("asr_backend")
    # Сохранённый бэкенд может быть не рабочим на свежей копии (например,
    # gigaam_e2e_rnnt без установленных torch/pyannote). Не тащим «мёртвый»
    # выбор в UI — отдаём установленный или дефолтный, иначе пайплайн падает
    # с ошибкой вида «GigaAM не установлен».
    if not isinstance(current, str) or not _usable(current):
        current = ab.installed_backend() or ab.DEFAULT_BACKEND
    return jsonify(
        {
            "backends": backends,
            "current": current,
            "installed": ab.installed_backend(),
            "gigaam_deps_ok": gigaam_ready,
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
    # Проект запускается как __main__ (pythonw main.py), поэтому _mutex_handle
    # живёт в __main__, а не в модуле `import main` (это был бы ВТОРОЙ экземпляр
    # с _mutex_handle=None — и мутекс остался б, новый процесс упёрся бы в
    # «PromptEar уже запущен»). Освобождаем в том же экземпляре до запуска
    # нового процесса.
    main_mod = sys.modules.get("__main__")
    release = getattr(main_mod, "_release_single_instance", None)
    if callable(release):
        try:
            release()
        except Exception:
            get_logger().warning("Не удалось освободить single-instance мутекс", exc_info=True)
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
_llm_install_last_progress: tuple[int, int | None] | None = None
_llm_install_lock = threading.Lock()
_llm_install_cancel = threading.Event()


def _llm_install_progress() -> dict | None:
    """Прогресс скачивания модели ИИ: {done_mb, total_mb, pct} или None."""
    if _llm_install_last_progress is None:
        return None
    done, total = _llm_install_last_progress
    done_mb = done / (1024 * 1024)
    out: dict = {"done_mb": round(done_mb, 1)}
    if total:
        out["total_mb"] = round(total / (1024 * 1024), 1)
        out["pct"] = round(min(100.0, done / max(total, 1) * 100), 1)
    return out


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
                "port_ok": True if llm_ok else llama_port_available(),
                "engine": LLM_ENGINE,
                "port": enhancer_mod._llama_actual_port or None,
                "error": error,
                "installing": _llm_install_running,
                "install_error": _llm_install_last_error,
                "install_progress": _llm_install_progress(),
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "llm_ok": False,
                "model_ok": False,
                "port_ok": False,
                "engine": LLM_ENGINE,
                "port": enhancer_mod._llama_actual_port or None,
                "error": str(exc),
                "installing": _llm_install_running,
                "install_error": _llm_install_last_error,
                "install_progress": _llm_install_progress(),
            }
        )


@app.route("/api/llm/download", methods=["POST"])
def llm_download():
    """Скачивает GGUF-модель ИИ в фоне и поднимает llama-server."""
    global _llm_install_running, _llm_install_last_error, _llm_install_last_progress
    if _llm_install_running:
        return jsonify({"installing": True})
    with _llm_install_lock:
        if _llm_install_running:
            return jsonify({"installing": True})
        _llm_install_running = True
        _llm_install_last_error = ""
        _llm_install_last_progress = None
        _llm_install_cancel.clear()

    def worker():
        global _llm_install_running, _llm_install_last_error, _llm_install_last_progress
        try:
            from core.installers import GemmaInstaller

            def on_progress(done: int, total: int | None) -> None:
                global _llm_install_last_progress
                _llm_install_last_progress = (done, total)

            GemmaInstaller.download_and_start(on_progress=on_progress, cancel=_llm_install_cancel)
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
        return requests.get(f"http://127.0.0.1:{port}/health", timeout=0.4).status_code == 200
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
