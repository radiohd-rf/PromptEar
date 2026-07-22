"""Flask сервер PromptEar — API + SSE."""

import json
import os
import queue
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from core.events import (
    CancelledEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    OllamaReadyEvent,
    ProgressEvent,
    SetBusyEvent,
    TranscribingEvent,
)
from core.models import AudioFile, PipelineConfig
from core.pipeline import run_pipeline as run_pipeline_core
from processing.enhancer import OllamaEnhancer
from processing.transcriber import Transcriber
from utils.files import find_supported_files, is_video_file
from utils.extract_audio import extract_audio
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
        case OllamaReadyEvent():
            return {
                "type": "ollama_ready",
                "ollama_ok": event.ollama_ok,
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
        try:
            emit_queue.put_nowait(_event_to_dict(event))
        except queue.Full:
            pass
        event_str = _event_to_dict(event)
        logger.info(f"[{task_id}] {json.dumps(event_str, ensure_ascii=False)}")

    cancel = threading.Event()
    task["cancel"] = cancel

    try:
        files = task["files"]
        config = PipelineConfig(
            output_format=task.get("output_format", "docx"),
            multi_pass=True,
            initial_prompt=task.get("initial_prompt", "") or None,
            qwen_available=True,
        )

        gpu_info = detect_and_report()
        emit(LogEvent(f"GPU: {json.dumps(gpu_info, ensure_ascii=False)}"))
        device = gpu_info["device"]

        transcriber = Transcriber()
        enhancer = None
        enhancer = OllamaEnhancer()
        ollama_ok, model_ok = enhancer.is_available()
        emit(OllamaReadyEvent(ollama_ok=ollama_ok, model_ok=model_ok))

        audio_files = [AudioFile(path=Path(f)) for f in files]
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
            enhancer=enhancer if ollama_ok and model_ok else None,
        )

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

    # извлекаем аудио из видео
    for p in list(all_supported):
        if is_video_file(p):
            try:
                wav_path = extract_audio(p)
                all_supported.remove(p)
                all_supported.append(wav_path)
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
        "qwen": True,
        "output_format": request.form.get("output_format", "docx"),
        "output_dir": UPLOAD_DIR,
        "status": "processing",
    }

    t = threading.Thread(target=_process_files, args=(task_id,), daemon=True)
    t.start()

    return jsonify({"task_id": task_id, "file_count": len(all_audio)})


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


@app.route("/api/gpu")
def gpu_info():
    info = detect_and_report()
    return jsonify(info)


@app.route("/api/ollama")
def ollama_check():
    try:
        enhancer = OllamaEnhancer()
        ollama_ok, model_ok = enhancer.is_available()
        return jsonify({"ollama_ok": ollama_ok, "model_ok": model_ok})
    except Exception as exc:
        return jsonify({"ollama_ok": False, "model_ok": False, "error": str(exc)})


@app.route("/api/open-output")
def open_output():
    import subprocess
    subprocess.Popen(["explorer", str(UPLOAD_DIR)])
    return jsonify({"ok": True})
