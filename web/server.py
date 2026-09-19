"""Flask сервер PromptEar — API + SSE."""

import contextlib
import json
import os
import queue
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from config import LLM_ENGINE
from core.events import (
    CancelledEvent,
    DoneEvent,
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
from processing.enhancer import create_enhancer
from processing.transcriber import Transcriber
from utils.extract_audio import extract_audio
from utils.files import find_supported_files, is_video_file, save_docx, save_text_output
from utils.gpu import detect_and_report
from utils.logger import get_logger

logger = get_logger()

WEB_DIR = Path(__file__).resolve().parent
BASE_DIR = WEB_DIR.parent
UPLOAD_DIR = BASE_DIR / "output"
UPLOAD_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("PROMPTEAR_PORT", 5000))

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

tasks: dict[str, dict] = {}


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
        with contextlib.suppress(queue.Full):
            emit_queue.put_nowait(_event_to_dict(event))
        event_str = _event_to_dict(event)
        logger.info(f"[{task_id}] {json.dumps(event_str, ensure_ascii=False)}")

    cancel = threading.Event()
    task["cancel"] = cancel
    task["results"] = {}
    task["skip_file"] = None
    task["status"] = "processing"

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
            llm_available=False,
            enhance_mode=task.get("enhance_mode", "auto"),
        )
        task["enhance_mode"] = config.enhance_mode

        gpu_info = detect_and_report()
        emit(LogEvent(f"GPU: {json.dumps(gpu_info, ensure_ascii=False)}"))

        transcriber = Transcriber()
        enhancer = None
        enhancer = create_enhancer()
        llm_ok, model_ok = enhancer.is_available()
        emit(LlmReadyEvent(llm_ok=llm_ok, model_ok=model_ok))
        config.llm_available = llm_ok and model_ok

        audio_files = []
        for f in files:
            orig = original_videos.get(f)
            audio_files.append(AudioFile(
                path=Path(f),
                original_path=Path(orig) if orig else None,
            ))
        emit(
            LogEvent(
                f"Добавлено {len(audio_files)} файлов"
            )
        )

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

        # удаляем загруженные исходные файлы (они не нужны, цель — docx)
        for f_path_str in task.get("uploaded_files", []):
            p = Path(f_path_str)
            if p.exists():
                p.unlink()

        emit(DoneEvent("Готово"))

    except Exception as exc:
        logger.error(f"Pipeline error: {exc}", exc_info=True)
        emit(ErrorEvent(str(exc)))
    finally:
        emit_queue.put_nowait({"type": "__done__"})


@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/api/files", methods=["POST"])
def upload_files():
    """Принимает файлы, создаёт задачу, запускает обработку."""
    if "files" not in request.files:
        return jsonify({"error": "no files"}), 400

    task_id = uuid.uuid4().hex[:12]
    saved = []
    for f in request.files.getlist("files"):
        if f.filename:
            dest = UPLOAD_DIR / f"{task_id}_{f.filename}"
            f.save(str(dest))
            saved.append(str(dest))

    if not saved:
        return jsonify({"error": "no valid files"}), 400

    # находим все поддерживаемые файлы
    all_supported = find_supported_files([Path(p) for p in saved])

    # извлекаем аудио из видео; запоминаем оригиналы для удаления после обработки
    original_videos: dict[str, str] = {}  # wav_path -> original_video_path
    for p in list(all_supported):
        if is_video_file(p):
            try:
                wav_path = extract_audio(p)
                all_supported.remove(p)
                all_supported.append(wav_path)
                original_videos[str(wav_path)] = str(p)
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
        "output_format": request.form.get("output_format", "docx"),
        "enhance_mode": request.form.get("enhance_mode", "auto"),
        "output_dir": UPLOAD_DIR,
        "original_videos": original_videos,
        "uploaded_files": saved,
        "status": "processing",
        "results": {},
    }

    t = threading.Thread(target=_process_files, args=(task_id,), daemon=True)
    t.start()

    return jsonify({
        "task_id": task_id,
        "file_count": len(all_audio),
        "enhance_mode": tasks[task_id]["enhance_mode"],
    })


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
    filepath = UPLOAD_DIR / f"{task_id}_{filename}"
    if not filepath.exists():
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
        results.append({
            "filename": result.audio.path.name,
            "text": result.text,
        })
    return jsonify({"results": results})


@app.route("/api/enhance/<task_id>/<filename>", methods=["POST"])
def enhance_file(task_id, filename):
    """Улучшает уже сохранённый черновик (режим ask) и перезаписывает файл."""
    from threading import Event

    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    result = next(
        (r for r in task.get("results", {}).values() if r.audio.path.name == filename),
        None,
    )
    if result is None:
        return jsonify({"error": "file result not found"}), 404

    try:
        enhancer = create_enhancer()
        llm_ok, model_ok = enhancer.is_available()
        if not (llm_ok and model_ok):
            return jsonify({"error": "LLM недоступен"}), 409
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

    try:
        original = result.text
        result.text = enhancer.enhance_multi_pass(
            original,
            task.get("initial_prompt", "") or "",
            progress_callback=mp_progress,
            cancel=cancel,
            stream_callback=mp_stream,
        )
    except Exception as exc:
        return jsonify({"error": f"Ошибка улучшения: {exc}"}), 500

    filepath = result.audio.original_path or result.audio.path
    out_path = filepath.with_suffix(f".{task.get('output_format', 'docx')}")
    if out_path.suffix.lower() == ".docx":
        save_docx(out_path, result.text)
    else:
        save_text_output(
            out_path,
            task.get("output_format", "docx"),
            result.text,
            segments=result.segments,
            duration=result.duration_sec,
        )
    result.output_path = out_path
    return jsonify({"text": result.text, "output_path": str(out_path)})


@app.route("/api/gpu")
def gpu_info():
    info = detect_and_report()
    return jsonify(info)


@app.route("/api/llm")
def llm_check():
    try:
        enhancer = create_enhancer()
        llm_ok, model_ok = enhancer.is_available()
        return jsonify({"llm_ok": llm_ok, "model_ok": model_ok, "engine": LLM_ENGINE})
    except Exception as exc:
        return jsonify({"llm_ok": False, "model_ok": False, "error": str(exc)})


@app.route("/api/open-output")
def open_output():
    import subprocess
    subprocess.Popen(["explorer", str(UPLOAD_DIR)])
    return jsonify({"ok": True})
