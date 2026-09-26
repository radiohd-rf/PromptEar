"""Транскрипция аудио через faster-whisper (CTranslate2)."""

import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path
from threading import Event
from typing import Any

from config import (
    CT2_CACHE,
    TRANSCRIBE_STALL_TIMEOUT_SEC,
    WHISPER_CONDITION_ON_PREVIOUS_TEXT,
    WHISPER_DEFAULT_MODEL,
    WHISPER_VAD_MIN_SILENCE_MS,
    WHISPER_VAD_SPEECH_PAD_MS,
)
from core.models import Segment


def ensure_faster_whisper():
    """Проверяет и устанавливает faster-whisper если нужно."""
    if importlib.util.find_spec("faster_whisper") is not None:
        return True

    print("faster-whisper не установлен. Установка...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "faster-whisper",
            "nvidia-cublas-cu12",
        ],
        timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if importlib.util.find_spec("faster_whisper") is None:
        print("ОШИБКА: Не удалось установить faster-whisper")
        return False
    return True


def _register_cublas_dlls() -> str | None:
    """Подключает cuBLAS (CUDA 12) для ctranslate2 и возвращает None или текст проблемы.

    Начиная с ctranslate2 4.4 wheel больше не содержит cuBLAS — только cuDNN;
    без него транскрибация на CUDA падает с RuntimeError про cublas64_12.dll.
    DLL берутся из официального пакета nvidia-cublas-cu12 (устанавливается
    вместе с faster-whisper), каталог bin которого добавляется в путь поиска.
    """
    import ctypes
    import os
    from pathlib import Path

    try:
        ctypes.WinDLL("cublas64_12.dll")
        ctypes.WinDLL("cublasLt64_12.dll")
        return None
    except OSError:
        pass
    candidates: list[Path] = []
    try:
        from importlib.metadata import distribution

        dist = distribution("nvidia-cublas-cu12")
        for f in dist.files or []:
            p = str(f)
            if p.endswith("cublas64_12.dll"):
                candidates.append(Path(str(dist.locate_file(p))).resolve().parent)
    except Exception:
        pass
    import sysconfig

    purelib = Path(sysconfig.get_paths().get("purelib") or "")
    if purelib.is_dir():
        candidates.append(purelib / "nvidia" / "cublas" / "bin")
        candidates.append(purelib / "nvidia" / "cublas" / "lib")
    for d in candidates:
        try:
            if (d / "cublas64_12.dll").is_file():
                os.add_dll_directory(str(d))
                ctypes.WinDLL("cublas64_12.dll")
                ctypes.WinDLL("cublasLt64_12.dll")
                return None
        except Exception:
            continue
    return "нет cuBLAS (CUDA 12). Установите пакет nvidia-cublas-cu12 или выключите GPU-ускорение"


class Transcriber:
    """Класс для транскрипции аудио через faster-whisper с фоновой загрузкой модели."""

    def __init__(self):
        self._model: Any = None
        self._loaded_alias: str | None = None
        self._lock = threading.Lock()
        self.device = "cpu"
        self.compute_type = "int8"
        self.gpu_unavailable_reason: str | None = None
        self._gpu_probe_done = False

    def gpu_probe(self) -> str | None:
        """Проверяет, реально ли доступна CUDA-транскрибация (без загрузки модели).

        Возвращает None, если GPU можно использовать, иначе текст причины.
        Результат кешируется; фактическое устройство видно в self.device.
        """
        if self._gpu_probe_done:
            return self.gpu_unavailable_reason
        self._gpu_probe_done = True
        try:
            from core import settings as settings_store

            if not settings_store.load().get("use_gpu", False):
                self.gpu_unavailable_reason = None
                return None
            import ctranslate2

            if ctranslate2.get_cuda_device_count() <= 0:
                self.gpu_unavailable_reason = "CUDA-устройство не найдено"
            else:
                self.gpu_unavailable_reason = _register_cublas_dlls()
        except Exception:
            self.gpu_unavailable_reason = "не удалось проверить GPU"
        if not self.gpu_unavailable_reason:
            self.device = "cuda"
            self.compute_type = "float16"
        return self.gpu_unavailable_reason

    def load_model(self, model_name: str | None = None) -> None:
        """Загружает модель Whisper (лениво, один раз).

        Имя модели берётся из settings.whisper_model (если не передано).
        При смене модели прежняя выгружается (`unload()`), чтобы ctranslate2
        не держал файлы — каталог старой версии можно удалять.
        Устройство: settings.use_gpu → "cuda" (если ctranslate2 видит GPU),
        иначе "cpu" (compute_type float16/int8).
        """
        from core import settings as settings_store
        from core import whisper_models as wm

        if model_name is None:
            from core import asr_backends as ab

            bk = self.current_backend()
            if ab.kind_of(bk) == "whisper":
                model_name = ab.variant_of(bk)
            else:
                model_name = settings_store.load().get("whisper_model") or WHISPER_DEFAULT_MODEL

        with self._lock:
            if self._model is not None and self._loaded_alias == model_name:
                return
            self.unload()  # смена модели: освободить файлы старой

            device = "cpu"
            compute_type = "int8"
            if settings_store.load().get("use_gpu", False) and self.gpu_probe() is None:
                device = "cuda"
                compute_type = "float16"

            from faster_whisper import WhisperModel

            # Установленной модели нет — скачиваем в плоскую папку
            # models/ct2/<repo>/ (как это делает DownloadManager) и убираем
            # лишние версии, чтобы installed_model() оставался достоверным.
            CT2_CACHE.mkdir(parents=True, exist_ok=True)
            if not wm.is_installed(model_name):
                from core.downloader import download_whisper_model

                download_whisper_model(model_name, wm.model_dir(model_name))
                wm.remove_others(model_name)
            source = str(wm.model_dir(model_name))
            self._model = WhisperModel(
                source,
                device=device,
                compute_type=compute_type,
                download_root=str(CT2_CACHE),
            )
            self._loaded_alias = model_name

    def load_model_async(self, model_name: str | None = None) -> None:
        """Запускает фоновую загрузку модели. Не блокирует."""
        if self._model is not None:
            return
        threading.Thread(target=self.load_model, args=(model_name,), daemon=True).start()

    def transcribe(self, audio_path: Path, cancel: Event | None = None, **kwargs) -> str:
        """Транскрибирует аудиофайл через faster-whisper. Ждёт загрузку модели если нужно."""
        text, segments, duration = self.transcribe_with_segments(
            audio_path, cancel=cancel, **kwargs
        )
        return text

    def current_backend(self) -> str:
        """Активный бэкенд распознавания из настроек (валидный)."""
        from core import asr_backends as ab
        from core import settings as settings_store

        bk = settings_store.load().get("asr_backend") or ab.DEFAULT_BACKEND
        return bk if ab.is_backend(bk) else ab.DEFAULT_BACKEND

    def transcribe_with_segments(
        self,
        audio_path: Path,
        cancel: Event | None = None,
        on_segment=None,
        **kwargs,
    ) -> tuple[str, list[Segment], float]:
        """Транскрибирует аудио и возвращает (текст, сегменты с таймкодами, длительность).

        on_segment вызывается по мере распознавания с накопленным списком сегментов
        (Segment со стартовым таймкодом) — для живого черновика в UI.

        Тяжёлый блокирующий вызов выполняется в фоновом потоке: при установке
        cancel метод сразу возвращает частичный результат, а брошенный поток
        догорает до ближайшей границы (whisper — сегмент, GigaAM — чанк).
        Доступ к модели сериализован: движки не потокобезопасны.
        """
        from core import asr_backends as ab
        from core import settings as settings_store

        language = kwargs.pop("language", "ru")
        beam_size = kwargs.pop("beam_size", 5)
        vad_filter = kwargs.pop("vad_filter", True)
        condition_on_previous_text = kwargs.pop(
            "condition_on_previous_text", WHISPER_CONDITION_ON_PREVIOUS_TEXT
        )
        vad_parameters = kwargs.pop(
            "vad_parameters",
            {
                "min_silence_duration_ms": WHISPER_VAD_MIN_SILENCE_MS,
                "speech_pad_ms": WHISPER_VAD_SPEECH_PAD_MS,
            },
        )

        parts: list[str] = []
        raw_segments: list[Segment] = []
        info_box: dict[str, Any] = {}
        result_box: dict[str, Any] = {}
        done = threading.Event()
        abandoned = threading.Event()
        backend = self.current_backend()
        use_gpu = settings_store.load().get("use_gpu", False)

        # Сторож зависших транскрибаций: whisper растёт через parts, GigaAM —
        # только через on_segment (result_box заполняется в самом конце),
        # поэтому прогрессом считаем и то, и другое. Без прогресса дольше
        # TRANSCRIBE_STALL_TIMEOUT_SEC — fail fast с понятной ошибкой вместо
        # вечного спина (брошенный поток догорит сам на границе чанка).
        progress_tick = [time.monotonic()]
        user_on_segment = on_segment

        def on_segment_tracked(segs) -> None:
            progress_tick[0] = time.monotonic()
            if user_on_segment is not None:
                user_on_segment(segs)

        def worker() -> None:
            try:
                progress_tick[0] = time.monotonic()  # поток стартовал
                if ab.kind_of(backend) == "gigaam":
                    if abandoned.is_set():
                        return
                    from processing import gigaam

                    res = gigaam.transcribe_with_segments(
                        audio_path,
                        ab.variant_of(backend),
                        use_gpu=use_gpu,
                        cancel=cancel,
                        on_segment=on_segment_tracked,
                    )
                    result_box["res"] = res
                    return
                self.load_model()
                with self._lock:
                    if abandoned.is_set():
                        return
                    segments, info = self._model.transcribe(
                        str(audio_path),
                        language=language,
                        beam_size=beam_size,
                        vad_filter=vad_filter,
                        vad_parameters=vad_parameters,
                        condition_on_previous_text=condition_on_previous_text,
                        **kwargs,
                    )
                    info_box["info"] = info
                    for seg in segments:
                        if abandoned.is_set():
                            break
                        if cancel is not None and cancel.is_set():
                            break
                        parts.append(seg.text)
                        parsed = Segment(start=seg.start, end=seg.end, text=seg.text)
                        raw_segments.append(parsed)
                        acc = list(raw_segments)
                        if not abandoned.is_set():
                            on_segment_tracked(acc)
                    duration = 0.0
                    if float(getattr(info, "duration", 0.0) or 0.0):
                        duration = float(info.duration)
                    elif raw_segments:
                        duration = float(raw_segments[-1].end or 0.0)
                    result_box["res"] = (" ".join(parts), raw_segments, duration)
            except BaseException as exc:
                result_box["err"] = exc
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True).start()
        while not done.wait(0.05):
            if cancel is not None and cancel.is_set():
                abandoned.set()
                break
            if time.monotonic() - progress_tick[0] > TRANSCRIBE_STALL_TIMEOUT_SEC:
                abandoned.set()
                mins = TRANSCRIBE_STALL_TIMEOUT_SEC // 60
                human = f"{mins} мин" if mins else f"{TRANSCRIBE_STALL_TIMEOUT_SEC} с"
                raise TimeoutError(
                    f"Транскрибация зависла: нет прогресса {human}. Возможно, "
                    "завис GPU-расчёт — попробуйте CPU-режим или файл покороче."
                )
        res = result_box.get("res")
        if res is not None:
            return res
        err = result_box.get("err")
        if err is not None:
            raise err
        return " ".join(parts), raw_segments, 0.0

    def unload(self):
        """Выгружает модель из памяти."""
        self._model = None
        self._loaded_alias = None
        try:
            from core import asr_backends as ab

            bk = self.current_backend()
            if ab.kind_of(bk) == "gigaam":
                from processing import gigaam

                gigaam.unload_engine(ab.variant_of(bk))
        except Exception:
            pass
