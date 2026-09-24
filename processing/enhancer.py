"""Улучшение текста через LLM-движок: llama.cpp (llama-server).

Единый интерфейс BaseEnhancer — 3-проходная система (очистка/стиль/структура).
"""

import contextlib
import json
import os
import re
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, Thread

import requests

from config import (
    ENHANCER_CHUNK_SIZE,
    GGUF_URL,
    LLAMA_CUDA_URL,
    LLAMA_CUDART_URL,
    LLAMA_DIR,
    LLAMA_GPU_LAYERS,
    LLAMA_SERVER_PORT,
    LLM_BASE_URL,
    LLM_CONTEXT,
    LLM_MODEL,
    LLM_MODEL_PATH,
    LLM_NUM_PREDICT,
    LLM_RETRIES,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    MODEL_IDLE_CHECK_SEC,
    MODEL_IDLE_TIMEOUT_SEC,
    MULTI_PASS_MAX_RATIO,
    MULTI_PASS_MIN_RATIO,
)
from processing.topic import detect_topic
from utils.logger import get_logger

PASS_LABELS = {
    "pass1": "Очистка (орфография, пунктуация, повторы)",
    "pass2": "Стиль (грамматика, согласование)",
    "pass3": "Структура (абзацы, диалоги, без воды)",
}

# Правило для промптов всех проходов, когда вход размечен таймкодами [MM:SS].
# Метка выполняет роль якоря: её нельзя удалять/менять, при перестановке
# фрагмента она переносится вместе с ним (Gemma уже видит примерные таймкоды
# Whisper в каждом куске входного текста).
TS_KEEP_RULE = (
    "- В тексте есть метки времени [MM:SS] в начале фрагментов\n"
    "- Каждая метка привязана к своему фрагменту и обязана сохраниться\n"
    "- НЕ удаляй метки, НЕ меняй числа в них и НЕ добавляй новых\n"
    "- Если переносишь фрагмент текста — переноси его вместе с меткой\n"
    "- Метки всегда идут в порядке возрастания времени\n"
    "- Метка стоит ТОЛЬКО в начале своего фрагмента, НЕ ставь её посреди предложения\n"
    "- Если после метки идёт строчная буква — это не начало предложения, оставь регистр как есть\n"
)

# Фактический порт, на который реально поднялся llama-server в этом процессе.
# Позволяет /api/llm видеть порт, выбранный авто-подбором или вручную, даже
# если константа LLAMA_SERVER_PORT (8080) занята чужим процессом.
_llama_actual_port: int | None = None

# Последняя причина, почему llama-server не запустился (модульная, потому что
# /api/llm создаёт свежий инстанс enhancer через create_enhancer()).
_llama_last_error: str | None = None


def get_llm_error() -> str | None:
    """Возвращает последнюю известную причину, почему llama-server не запустился."""
    return _llama_last_error


def stop_llama_server() -> None:
    """Останавливает llama-server (например, при выключении «Использовать GPU»).

    Убитый процесс освобождает видеопамять, занятую моделью (-ngl). Модульный
    хелпер, чтобы web/server.py не лез в статический метод класса.
    """
    LlamaCppEnhancer._stop_llama_server()


def _llama_cmdline() -> str:
    """Cmdline запущенного llama-server ('' — процесс не найден).

    Нужен, чтобы отличать GPU-режим (-ngl > 0) от CPU (-ngl 0) после
    перезапуска приложения, когда llama-server выживает: модель тогда
    остаётся там, куда её загрузили, независимо от текущей галочки GPU.
    """
    try:
        out = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='llama-server.exe'",
                "get",
                "CommandLine",
                "/value",
            ],
            capture_output=True,
            timeout=5,
            check=False,
            # Без флага каждая проверка (а /api/llm опрашивается UI каждые
            # 5 с) вспыхивает консольным окном поверх приложения.
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except OSError:
        return ""
    return out.replace(b"\x00", b"").decode("ascii", "ignore")


def _llama_gpu_mode_running() -> bool | None:
    """Запущен ли живой llama-server с GPU-оффлоадом (-ngl > 0).

    True/False — режим определён по cmdline процесса; None — процесс не найден
    или определить не удалось. Нужно, чтобы после перезапуска приложения (когда
    llama-server выживает) при включённом GPU не переиспользовать движок,
    работающий в CPU-режиме (-ngl 0) — модель тогда остаётся на процессоре.
    """
    m = re.search(r"-ngl\s+(\d+)", _llama_cmdline())
    if m is None:
        return None
    return int(m.group(1)) > 0


# --- Гашение llama-server при простое -------------------------------------
# Движок дорогой (3 ГБ VRAM), а висит detached вечно. Сторож раз в
# MODEL_IDLE_CHECK_SEC смотрит: если движком реально пользовались в этой
# сессии (есть управляемые PID), генераций в полёте нет и простой дольше
# MODEL_IDLE_TIMEOUT_SEC — гасит процесс. Опросы /api/llm статусом
# активностью НЕ считаются (иначе UI-пулинг каждые 5 с не давал бы уснуть).
_llama_last_used: float | None = None  # monotonic() последнего обращения
_llama_inflight = 0  # активных генераций — рабочий движок не гасим
_llama_pinned = 0  # задач, которым движок понадобится (сторож не гасит)
_llama_managed_pids: set[int] = set()  # PID, запущенные/подобранные сессией
_llama_idle_watcher_started = False
_llama_state_lock = Lock()


def _touch_llama() -> None:
    """Отметить реальное обращение к движку (сбрасывает простой)."""
    global _llama_last_used
    _llama_last_used = time.monotonic()


def pin_llama_server() -> None:
    """Запрет гашения на время задачи (auto-режим: движок понадобится в конце)."""
    global _llama_pinned
    _llama_pinned += 1
    _touch_llama()


def unpin_llama_server() -> None:
    """Снять запрет гашения (вызывать в finally задачи)."""
    global _llama_pinned
    _llama_pinned = max(0, _llama_pinned - 1)


@contextlib.contextmanager
def _llama_call():
    """Учёт активной генерации: сторож ждёт её завершения."""
    global _llama_inflight
    _llama_inflight += 1
    _touch_llama()
    try:
        yield
    finally:
        _llama_inflight -= 1


def _llama_pids() -> list[int]:
    """PID всех запущенных llama-server.exe (без всплытия окна)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "llama-server.exe":
            with contextlib.suppress(ValueError):
                pids.append(int(parts[1]))
    return pids


def _manage_llama_pid(pid: int | None) -> None:
    """Взять PID под надзор сторожа простоя."""
    global _llama_managed_pids
    if pid is not None:
        _llama_managed_pids.add(pid)


def _ensure_llama_idle_watcher() -> None:
    """Запускает сторожа простоя (один раз за процесс)."""
    global _llama_idle_watcher_started
    with _llama_state_lock:
        if _llama_idle_watcher_started:
            return
        _llama_idle_watcher_started = True
    Thread(target=_llama_idle_watcher, name="llama-idle", daemon=True).start()


def _llama_idle_watcher() -> None:
    """Фон: гасит llama-server после MODEL_IDLE_TIMEOUT_SEC простоя."""
    while True:
        time.sleep(MODEL_IDLE_CHECK_SEC)
        # Сторож не должен умирать.
        with contextlib.suppress(Exception):
            _llama_idle_check()


def _llama_idle_check() -> None:
    """Одна проверка сторожа (вынесена для тестируемости)."""
    global _llama_actual_port, _llama_managed_pids
    if (
        not _llama_managed_pids
        or _llama_inflight > 0
        or _llama_pinned > 0
        or _llama_last_used is None
    ):
        return
    if time.monotonic() - _llama_last_used < MODEL_IDLE_TIMEOUT_SEC:
        return
    # Чужие процессы не трогаем: гасим только свои, и только если живы.
    alive = [p for p in _llama_managed_pids if p in _llama_pids()]
    _llama_managed_pids = set(alive)
    if not alive:
        _llama_actual_port = None
        return
    mins = MODEL_IDLE_TIMEOUT_SEC // 60
    for pid in alive:
        if _llama_inflight > 0:  # генерация стартовала, пока гасили
            return
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    _llama_managed_pids = set()
    _llama_actual_port = None
    get_logger().info(f"llama-server остановлен после {mins} мин простоя")


class BaseEnhancer(ABC):
    """Общий интерфейс улучшения текста."""

    @abstractmethod
    def is_available(self) -> tuple[bool, bool]:
        """Возвращает (engine_ready, model_ready)."""

    @abstractmethod
    def enhance_multi_pass(
        self,
        text: str,
        topic: str = "",
        progress_callback=None,
        cancel: Event | None = None,
        stream_callback=None,
        timestamps: bool = False,
    ) -> str:
        """Улучшает текст (полный режим). stream_callback(text, pass_no, chunk_no, chunk_total).

        timestamps=True — во входе есть таймкод-метки [MM:SS], их нужно сохранить.
        """

    @abstractmethod
    def install(self, progress_callback=None) -> bool:
        """Устанавливает движок и модель. True при успехе."""

    @abstractmethod
    def get_engine_name(self) -> str:
        """Имя движка."""

    def translate(
        self,
        text: str,
        language_code: str,
        language_name: str = "",
        cancel: Event | None = None,
    ) -> str:
        """Переводит текст на указанный язык (однопроходным запросом).

        По умолчанию недоступен.
        """
        raise NotImplementedError(
            f"Перевод не поддерживается движком {self.get_engine_name()}"
        )


class LlamaCppEnhancer(BaseEnhancer):
    """Улучшение текста через llama-server (OpenAI-совместимый API) + gemma-4-E2B."""

    def __init__(self, model: str = LLM_MODEL, port: int | None = None):
        self.model = model
        self.base_url = LLM_BASE_URL
        self.port_override = port
        self.last_error: str | None = None
        self._session: requests.Session | None = None
        # Сводку offload llama-server логируем ровно один раз за сессию (после
        # первого успешного /health); иначе она дублировалась бы на каждый
        # перезапуск движка.
        self._offload_summary_logged = False
        # Последняя сформированная сводка offload (дубль строки из app.log).
        self._offload_summary: str = ""

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _resolved_base_url(self) -> str:
        """Реальный адрес llama-server: фактически выбранный порт важнее конфига.

        Свежий enhancer (create_enhancer) не знает, что пользователь задал
        другой порт или что движок нашёлся на свободном порту из диапазона —
        поэтому для запросов берём _llama_actual_port, если он уже известен.
        """
        if _llama_actual_port is not None:
            return f"http://127.0.0.1:{_llama_actual_port}"
        return self.base_url

    def get_engine_name(self) -> str:
        return "llama"

    def is_available(self) -> tuple[bool, bool]:
        """Проверяет запущен ли llama-server и есть ли GGUF-файл.

        Возвращает (server_ok, model_ok). model_ok считается по наличию файла
        модели НЕЗАВИСИМО от статуса сервера: файл может быть на месте, а
        llama-server ещё не запущен (ленивый старт по запросу) — и это не
        значит, что модель "не установлена". Сначала проверяется фактический
        порт (авто-подобранный или выбранный вручную), затем сканируется
        диапазон LLAMA_SERVER_PORT..+50 на предмет уже запущенного llama-server.
        """
        global _llama_actual_port
        model_ok = LLM_MODEL_PATH.exists()
        prime = (_llama_actual_port, self.port_override, LLAMA_SERVER_PORT)
        candidates = [p for p in prime if p is not None]
        for p in range(LLAMA_SERVER_PORT, LLAMA_SERVER_PORT + 50):
            if p not in candidates:
                candidates.append(p)

        # 1) параллельно выясняем все живые llama-порты (быстро, даже если
        #    почти все порты заняты чужими процессами без http)
        alive = self._health_ok_ports()
        if not alive:
            return False, model_ok
        # Желаем GPU, но единственный живой llama-server крутится в CPU-режиме
        # (-ngl 0, например пережил рестарт приложения). Такой движок считаем
        # недоступным: вызывающий код (_ensure_llm / /api/llm/port) перезапустит
        # его с GPU-оффлоадом, а не будет по-тихому гонять модель на процессоре.
        want_gpu = self._use_gpu_setting() and self.cuda_build_present()
        if want_gpu and _llama_gpu_mode_running() is False:
            self.last_error = (
                "llama-server работает в CPU-режиме, а включён «Использовать GPU» — "
                "движок перезапускается"
            )
            return False, model_ok
        # 2) выбираем первый приоритетный из живых (порядок кандидатов сохранён)
        for port in candidates:
            if port in alive:
                _llama_actual_port = port
                self.base_url = f"http://127.0.0.1:{port}"
                return True, model_ok
        return False, model_ok

    def _health(self, port: int, timeout: float = 0.4) -> bool:
        """Быстрая проверка: отвечает ли порт /health.

        Сначала TCP-коннект с малым таймаутом (закрытый/слушающий без http
        порт отсеивается за миллисекунды), затем HTTP-запрос на живой порт.
        """
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                pass
        except OSError:
            return False
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _health_ok_ports(self, timeout: float = 0.4) -> set[int]:
        """Параллельно проверяет /health на всём диапазоне портов.

        Возвращает множество портов, где llama-server жив, за время одного
        таймаута (а не 50 × timeout), используя пул потоков.
        """
        ports = list(range(LLAMA_SERVER_PORT, LLAMA_SERVER_PORT + 50))
        with ThreadPoolExecutor(max_workers=min(32, len(ports))) as pool:
            results = pool.map(lambda p: (p, self._health(p, timeout)), ports)
        return {p for p, ok in results if ok}

    # ── Одиночный проход (для обратной совместимости) ──────────────────────

    def enhance(self, text: str, context: str = "") -> str:
        """Улучшает распознанный текст за один проход (старая версия)."""
        prompt = (
            "Ты — редактор транскрипции аудио.\n\n"
            "Правила:\n"
            "- Цель: минимальные изменения. Меняй только то, что точно ошибочно\n"
            "- Если сомневаешься — оставь как есть\n"
            "- Исправь только орфографию, пунктуацию и повторы слов подряд\n"
            "- В тексте МОЖЕТ быть несколько говорящих — не удаляй реплики ни одного\n"
            "- ЗАПРЕЩЕНО менять слова, порядок слов, стиль, структуру\n"
            "- ЗАПРЕЩЕНО удалять предложения, факты, имена, числа, даты\n"
            "- ЗАПРЕЩЕНО пересказывать, сокращать, обобщать\n"
            "- Не добавляй ничего от себя\n"
            "- Верни только исправленный текст, без пояснений"
        )
        if context:
            prompt += f"\nКонтекст: {context}"
        prompt += f"\n\nТекст:\n{text}"
        return self._call_llm(prompt)

    # ── 3-проходная система ───────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str, max_size: int) -> list[str]:
        """Режет текст на чанки по границам предложений, каждый ≤ max_size символов."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []
        current: list[str] = []
        curr_len = 0
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if current and curr_len + len(sent) > max_size:
                chunks.append(" ".join(current))
                current, curr_len = [], 0
            current.append(sent)
            curr_len += len(sent)
        if current:
            chunks.append(" ".join(current))
        return chunks or [text]

    def translate(
        self,
        text: str,
        language_code: str,
        language_name: str = "",
        cancel: Event | None = None,
    ) -> str:
        """Переводит текст на указанный язык.

        Однопроходный перевод (без 3-проходной системы), длинные тексты
        дробятся на чанки по границам предложений — каждый переводится отдельно.
        language_code — ISO-код, language_name — человекочитаемое имя языка
        (без него некоторые модели путают коды вроде 'be'/'kk' и переводят
        «на код», т.е. по умолчанию на английский).
        """
        if not text.strip():
            return text
        target = language_name.strip() or language_code
        chunks = self._chunk_text(text, ENHANCER_CHUNK_SIZE)
        translated = []
        for chunk in chunks:
            if cancel is not None and cancel.is_set():
                break
            chunk = self._protect_speakers(chunk)
            prompt = (
                "Ты — профессиональный переводчик.\n\n"
                "Переведи текст на следующий язык:\n"
                f"- язык: {target} (код '{language_code}')\n"
                "Правила:\n"
                "- Сохрани смысл и структуру абзацев\n"
                "- Может быть несколько говорящих — не удаляй реплики\n"
                "- Имена, числа, даты оставь максимально близко к оригиналу\n"
                "- Если в тексте есть метки времени [MM:SS] — сохрани их как есть\n"
                "- Верни только перевод, без пояснений, вступлений и комментариев\n\n"
                f"Текст:\n{chunk}"
            )
            res = self._call_llm(prompt)
            translated.append(self._restore_speakers(res.strip()))
        return "\n\n".join(t for t in translated if t)

    def enhance_multi_pass(
        self,
        text: str,
        topic: str = "",
        progress_callback=None,
        cancel: Event | None = None,
        stream_callback=None,
        timestamps: bool = False,
    ) -> str:
        """3-проходное улучшение: очистка → стиль → структура.

        Длинные тексты дробятся на чанки, каждый обрабатывается независимо.
        stream_callback(text_so_far, pass_no, chunk_no, chunk_total) вызывается
        по мере генерации каждого прохода («модель печатает»).

        timestamps=True — во входе есть метки [MM:SS] (от Whisper): промпты
        получают правило не удалять и не переставлять их, метка «прилипает»
        к своему фрагменту даже после переструктурирования.
        """
        if not text.strip():
            return text

        if not topic:
            topic = detect_topic(text)

        chunks = self._chunk_text(text, ENHANCER_CHUNK_SIZE)
        passes = [
            ("pass1", self._pass_cleanup),
            ("pass2", self._pass_style),
            ("pass3", self._pass_structure),
        ]

        processed = []
        for idx, chunk in enumerate(chunks):
            if cancel is not None and cancel.is_set():
                break
            chunk = self._protect_speakers(chunk)

            for name, pass_fn in passes:
                if cancel is not None and cancel.is_set():
                    break
                label = f"Проход {name[4:]}/3: {PASS_LABELS.get(name, name)}"
                if len(chunks) > 1:
                    label = f"Чанк {idx + 1}/{len(chunks)}: {label}"
                if progress_callback:
                    progress_callback(label)

                if stream_callback is not None:
                    try:
                        result = pass_fn(
                            chunk,
                            topic,
                            cancel=cancel,
                            stream_callback=stream_callback,
                            keep_timestamps=timestamps,
                        )
                    except Exception:
                        if progress_callback:
                            progress_callback(
                                f"  Ошибка на проходе {name[4:]}, сохранён предыдущий"
                            )
                        result = chunk
                else:
                    try:
                        result = pass_fn(chunk, topic, keep_timestamps=timestamps)
                    except Exception:
                        if progress_callback:
                            progress_callback(
                                f"  Ошибка на проходе {name[4:]}, сохранён предыдущий"
                            )
                        result = chunk

                if self._result_too_short(result, chunk):
                    if progress_callback:
                        progress_callback(
                            "  Результат слишком короткий (<60% длины), сохранён предыдущий"
                        )
                else:
                    chunk = result

            if cancel is not None and cancel.is_set():
                break
            processed.append(self._restore_speakers(chunk))

        return "\n\n".join(processed)

    # ── Отдельные проходы ──────────────────────────────────────────────────

    def _call_llm(self, prompt: str, timeout: int = LLM_TIMEOUT) -> str:
        """Отправляет запрос в llama-server (OpenAI API) и возвращает ответ.

        При таймауте делает повтор (фикс бага #15).
        """
        with _llama_call():
            return self._do_call_llm(prompt, timeout)

    def _do_call_llm(self, prompt: str, timeout: int) -> str:
        """Тело _call_llm (без учёта простоя — см. _llama_call)."""
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_NUM_PREDICT,
            "stream": False,
        }
        url = f"{self._resolved_base_url()}/v1/chat/completions"
        attempts = LLM_RETRIES + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                r = self._get_session().post(
                    url,
                    json=payload,
                    timeout=timeout,
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(2)
        raise RuntimeError(f"llama-server недоступен: {last_exc}")

    def _call_llm_stream(
        self,
        prompt: str,
        on_token,
        cancel: Event | None = None,
        timeout: int = LLM_TIMEOUT,
    ) -> str:
        """Отправляет запрос со стримингом токенов (SSE).

        on_token(text_so_far) вызывается с накопленным текстом по мере генерации.
        Флаг cancel прерывает запрос: соединение закрывается, генерация обрывается.
        Возвращает полный сгенерированный текст.
        """
        with _llama_call():
            return self._do_call_llm_stream(prompt, on_token, cancel, timeout)

    def _do_call_llm_stream(
        self,
        prompt: str,
        on_token,
        cancel: Event | None = None,
        timeout: int = LLM_TIMEOUT,
    ) -> str:
        """Тело _call_llm_stream (без учёта простоя — см. _llama_call)."""
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_NUM_PREDICT,
            "stream": True,
        }
        url = f"{self._resolved_base_url()}/v1/chat/completions"
        last_exc: Exception | None = None
        for attempt in range(LLM_RETRIES + 1):
            try:
                with self._get_session().post(
                    url,
                    json=payload,
                    timeout=timeout,
                    stream=True,
                ) as r:
                    r.raise_for_status()
                    parts: list[str] = []
                    last_emit = 0
                    for raw in r.iter_lines(decode_unicode=False):
                        if cancel is not None and cancel.is_set():
                            return "".join(parts).strip()
                        if not raw:
                            continue
                        # header без charset (text/event-stream) заставляет requests
                        # угадывать кодировку и ломать кириллицу — декодируем вручную
                        line = raw.decode("utf-8")
                        data = line[6:] if line.startswith("data: ") else line
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        piece = delta.get("content")
                        if piece:
                            parts.append(piece)
                            emitted = "".join(parts)
                            if len(emitted) - last_emit >= 40:
                                last_emit = len(emitted)
                                on_token(emitted)
                    if parts:
                        on_token("".join(parts))
                    return "".join(parts).strip()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if cancel is not None and cancel.is_set():
                    return ""
                if attempt < LLM_RETRIES:
                    time.sleep(2)
        raise RuntimeError(f"llama-server недоступен: {last_exc}")

    def _pass_cleanup(
        self,
        text: str,
        topic: str,
        cancel: Event | None = None,
        stream_callback=None,
        keep_timestamps: bool = False,
    ) -> str:
        """Проход 1: орфография, пунктуация, повторы."""
        prompt = (
            "Ты — редактор. Вход — сырая транскрипция с ошибками распознавания.\n\n"
            "Правила:\n"
            "- Цель: минимальные изменения. Меняй только то, что точно ошибочно\n"
            "- Если сомневаешься — оставь как есть\n"
            "- Исправь только орфографию, пунктуацию и повторы слов подряд\n"
            "- В тексте МОЖЕТ быть несколько говорящих — не удаляй реплики ни одного\n"
            "- ЗАПРЕЩЕНО менять слова, порядок слов, стиль, структуру\n"
            "- ЗАПРЕЩЕНО удалять предложения, факты, имена, числа, даты\n"
            "- ЗАПРЕЩЕНО пересказывать, сокращать, обобщать, переформулировать\n"
            "- Не добавляй ничего от себя\n"
            "- Не пиши пояснений, выводов, комментариев — верни только исправленный текст\n\n"
            "Проверь себя: количество предложений в ответе должно быть "
            "равно количеству предложений во входе."
        )
        if keep_timestamps:
            prompt += "\n" + TS_KEEP_RULE
        if topic:
            prompt += f"\nТема: {topic}"
        prompt += f"\n\nТекст:\n{text}"
        if stream_callback is not None:
            return self._call_llm_stream(prompt, stream_callback, cancel=cancel)
        return self._call_llm(prompt)

    def _pass_style(
        self,
        text: str,
        topic: str,
        cancel: Event | None = None,
        stream_callback=None,
        keep_timestamps: bool = False,
    ) -> str:
        """Проход 2: грамматика, стиль, согласование."""
        prompt = (
            "Ты — редактор. Вход — текст после автоматической очистки, "
            "но с грамматическими ошибками.\n\n"
            "Правила:\n"
            "- Цель: минимальные изменения. Меняй только то, что точно ошибочно\n"
            "- Если сомневаешься — оставь как есть\n"
            "- В тексте МОЖЕТ быть несколько говорящих — не удаляй реплики ни одного\n"
            "- Исправь только согласование окончаний, падежей, времён глаголов\n"
            "- Если порядок слов явно нарушен — исправь, иначе оставь как есть\n"
            "- ЗАПРЕЩЕНО менять лексику, стиль, структуру абзацев\n"
            "- ЗАПРЕЩЕНО удалять или пересказывать факты, имена, числа, даты\n"
            "- ЗАПРЕЩЕНО добавлять от себя, давать пояснения или комментарии\n"
            "- Верни только исправленный текст, без вступлений и заключений\n\n"
            "Проверь себя: количество предложений в ответе должно быть "
            "равно количеству предложений во входе."
        )
        if keep_timestamps:
            prompt += "\n" + TS_KEEP_RULE
        if topic:
            prompt += f"\nТема: {topic}"
        prompt += f"\n\nТекст:\n{text}"
        if stream_callback is not None:
            return self._call_llm_stream(prompt, stream_callback, cancel=cancel)
        return self._call_llm(prompt)

    def _pass_structure(
        self,
        text: str,
        topic: str,
        cancel: Event | None = None,
        stream_callback=None,
        keep_timestamps: bool = False,
    ) -> str:
        """Проход 3: разбивка на абзацы, оформление диалогов, чистка слов-паразитов."""
        prompt = (
            "Ты — редактор. Вход — текст с корректной орфографией и грамматикой.\n\n"
            "Что можно делать:\n"
            "- Разбить на абзацы по смене темы или говорящего (добавить пустые строки)\n"
            "- Если есть диалоги/прямая речь — каждый реплика с новой строки\n"
            "- Объединить короткие однострочные абзацы в связные блоки\n"
            "- Удалить слова-паразиты и повторы-заполнители "
            "(такие как: это самое, ну, типа, как бы, вот, значит, в общем, короче), "
            "если они не несут смысла — текст без воды\n\n"
            "ЗАПРЕЩЕНО:\n"
            "- Менять слова, порядок слов, стиль, грамматику\n"
            "- Удалять или пересказывать факты, имена, числа, даты\n"
            "- Удалять реплики любого из говорящих — сохрани всех спикеров\n"
            "- Добавлять от себя, давать пояснения или выводы\n\n"
            "Цель: минимальные изменения. Если нечего менять в структуре — верни текст как есть.\n"
            "Верни только исправленный текст — ни слова лишнего."
        )
        if keep_timestamps:
            prompt += "\n" + TS_KEEP_RULE
        if topic:
            prompt += f"\nТема: {topic}"
        prompt += f"\n\nТекст:\n{text}"
        if stream_callback is not None:
            return self._call_llm_stream(prompt, stream_callback, cancel=cancel)
        return self._call_llm(prompt)

    @staticmethod
    def _result_too_short(result: str, original: str) -> bool:
        """Проверяет, не упростила ли модель текст сильнее допустимого.

        Возвращает True, если длина результата <60% от оригинала
        или если результат длиннее оригинала >140% (модель «фантазирует»).
        """
        if not result or len(result) < 5:
            return True
        ratio = len(result) / max(len(original), 1)
        return ratio < MULTI_PASS_MIN_RATIO or ratio > MULTI_PASS_MAX_RATIO

    @staticmethod
    def _protect_speakers(text: str) -> str:
        """Заменяет диалоговые тире на явные метки [СПИКЕР N]:.

        Чтобы модель не удаляла реплики, превращаем «— текст» в «[СПИКЕР 1]: текст».
        Учитывает строки с префиксом таймкода «[00:05] — текст».
        """
        lines = text.split("\n")
        speaker_count = 0
        result = []
        for line in lines:
            stripped = line.strip()
            m = re.match(
                r"^(\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?[—–-]\s+(.+)",
                stripped,
            )
            if m:
                speaker_count += 1
                prefix = m.group(1) or ""
                result.append(f"{prefix}[СПИКЕР {speaker_count}]: {m.group(2)}")
            else:
                result.append(line)
        return "\n".join(result)

    @staticmethod
    def _restore_speakers(text: str) -> str:
        """Обратное преобразование: [СПИКЕР N]: → —."""
        return re.sub(r"\[СПИКЕР \d+\]:\s?", "— ", text)

    # ── Установка ──────────────────────────────────────────────────────────

    @staticmethod
    def _use_gpu_setting() -> bool:
        """Уважает галочку «Использовать GPU» (core/settings)."""
        from core import settings as settings_store

        return bool(settings_store.load().get("use_gpu", False))

    @staticmethod
    def cuda_build_present() -> bool:
        """Поставлена ли CUDA-сборка llama.cpp (ggml-cuda.dll)."""
        return any(LLAMA_DIR.rglob("ggml-cuda*.dll"))

    @staticmethod
    def _stop_llama_server() -> None:
        """Останавливает llama-server (иначе он держит DLL сборки)."""
        subprocess.run(
            ["taskkill", "/IM", "llama-server.exe", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _fetch_build(
        self,
        url: str,
        label: str,
        progress_callback=None,
        on_download: Callable[[int, int | None], None] | None = None,
        require_server: bool = True,
    ) -> Path | None:
        """Скачивает zip-сборку llama.cpp, останавливает старый сервер и
        распаковывает сборку поверх текущей. Возвращает путь к llama-server.exe
        (None, если require_server=False — дополняющий архив, например cudart).

        on_download(done, total) — байтовый прогресс скачивания (как fetch_file).
        """
        if progress_callback:
            progress_callback(f"Скачивание {label}...")

        zip_path = LLAMA_DIR / "llama.zip"
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(f"  Попытка {attempt}/{max_retries}...")
                r = requests.get(url, stream=True, timeout=(15, 120))
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0) or None
                done = 0
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if progress_callback and total:
                                progress_callback(f"  {done / 1e6:.0f} / {total / 1e6:.0f} МБ")
                            if on_download:
                                on_download(done, total)
                break
            except requests.exceptions.ConnectionError:
                if attempt == max_retries:
                    raise
                if progress_callback:
                    progress_callback("  Сетевая ошибка, повтор через 3 сек...")
                time.sleep(3)

        # Перезаписать файлы сборки можно только после остановки llama-server
        # (Windows держит загруженные DLL).
        self._stop_llama_server()

        if progress_callback:
            progress_callback("Распаковка llama.cpp...")
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(LLAMA_DIR)
        zip_path.unlink()

        server_exe = None
        if require_server:
            server_exe = LLAMA_DIR / "llama-server.exe"
            if not server_exe.exists():
                for candidate in LLAMA_DIR.rglob("llama-server.exe"):
                    server_exe = candidate
                    break
            if not server_exe.exists():
                raise RuntimeError("llama-server.exe не найден в архиве llama.cpp")
        return server_exe

    def download_cuda_build(
        self,
        progress_callback=None,
        on_download: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Ставит CUDA-сборку llama.cpp + CUDA-рантайм (самодостаточно)."""
        if self.cuda_build_present():
            return
        # 1) llama-server, ggml-cuda.dll и т.д.
        self._fetch_build(
            LLAMA_CUDA_URL,
            "CUDA-сборка llama.cpp",
            progress_callback,
            on_download=on_download,
        )
        # 2) CUDA-рантайм (cudart64_13, cublas64_13, cublasLt64_13) — без него
        #    ggml-cuda.dll не загрузится (его нет в бинарном архиве).
        self._fetch_build(
            LLAMA_CUDART_URL,
            "CUDA-рантайм",
            progress_callback,
            on_download=on_download,
            require_server=False,
        )

    def install(self, progress_callback=None) -> bool:
        """Скачивает llama.cpp (cpu или cuda — зависит от «Использовать GPU»),
        распаковывает и запускает llama-server. Возвращает True при успехе.
        """
        # Подбираем источник: при «Использовать GPU» ставим CUDA-сборку (CPU
        # при установке не нужна — модель после GPU-сборки работает и на CPU
        # через -ngl 0, а переключение вниз не качает ничего).
        want_cuda = self._use_gpu_setting() and not self.cuda_build_present()
        url = LLAMA_CUDA_URL if want_cuda else None
        server_exe = None
        if url is not None:
            server_exe = self._fetch_build(
                url,
                "CUDA-сборка llama.cpp",
                progress_callback,
            )
        else:
            if progress_callback:
                progress_callback("Используем уже установленную сборку llama.cpp")
            server_exe = self._find_server_exe()
        if server_exe is None:
            raise RuntimeError("llama-server.exe не найден в архиве llama.cpp")

        if not LLM_MODEL_PATH.exists():
            raise RuntimeError(
                f"Модель не найдена: {LLM_MODEL_PATH}. Положите GGUF в папку models/llm/"
            )

        return self._start_server(server_exe, progress_callback)

    def _pick_port(self, preferred: int | None = None) -> int | None:
        """Возвращает свободный TCP-порт.

        Сначала пробует self.port_override (порт, который пользователь указал
        вручную), затем preferred, затем LLAMA_SERVER_PORT и далее подряд.
        Если свободных нет — None, чтобы не запускать llama-server на занятый
        порт и не врать про /health.
        """
        candidates = [
            p for p in (self.port_override, preferred, LLAMA_SERVER_PORT) if p is not None
        ]
        candidates += [
            p for p in range(LLAMA_SERVER_PORT, LLAMA_SERVER_PORT + 50) if p not in candidates
        ]
        for port in candidates:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # НЕ ставить SO_REUSEADDR: на Windows он позволяет bind поверх
                # активно слушающего порта (WinError 10048 появляется только без
                # него), из-за чего llama-server потом не может занять порт.
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return None

    def _find_running_llama(self, preferred: int | None = None) -> int | None:
        """Ищет уже запущенный llama-server в диапазоне портов.

        Проверяет /health на каждом кандидате. Если сервер отвечает — возвращает
        его порт (Чтобы не поднимать второй экземпляр модели в память).
        """
        candidates = []
        for p in (self.port_override, preferred, LLAMA_SERVER_PORT):
            if p is not None and p not in candidates:
                candidates.append(p)
        candidates += [
            p for p in range(LLAMA_SERVER_PORT, LLAMA_SERVER_PORT + 50) if p not in candidates
        ]
        # параллельная проверка: сначала живые, затем первый по приоритету
        alive = self._health_ok_ports()
        for port in candidates:
            if port in alive:
                return port
        return None

    def _start_server(
        self, server_exe, progress_callback=None, prefer_port: int | None = None
    ) -> bool:
        """Запускает llama-server и ждёт /health.

        Возвращает True при успехе; причина провала сохраняется в self.last_error
        и доступна через /api/llm. prefer_port — конкретный порт, если пользователь
        выбрал его вручную.
        """
        global _llama_actual_port, _llama_last_error
        self.last_error = None
        if self.is_available()[0]:
            _touch_llama()
            return True

        # Если llama-server уже запущен в диапазоне портов (например, его поднял
        # пользователь вручную на 8081, пока 8080 занят httpd) — используем его,
        # а не спавним второй экземпляр с загрузкой модели в память.
        # Исключение: желаем GPU-оффлоад, а живой движок крутится в CPU-режиме
        # (-ngl 0, пережил рестарт приложения) — таких переиспользовать нельзя,
        # иначе «Использовать GPU» включено, а модель снова на процессоре.
        want_gpu = self._use_gpu_setting() and self.cuda_build_present()
        existing = self._find_running_llama(prefer_port)
        if existing is not None:
            if not want_gpu or _llama_gpu_mode_running() is not False:
                _llama_actual_port = existing
                self.base_url = f"http://127.0.0.1:{existing}"
                # Одинокий процесс берём под надзор сторожа простоя; если
                # серверов несколько — чей он, непонятно, не трогаем.
                pids = _llama_pids()
                if len(pids) == 1:
                    _manage_llama_pid(pids[0])
                _touch_llama()
                _ensure_llama_idle_watcher()
                return True
            # CPU-движок при желаемом GPU — убиваем и стартуем заново с -ngl.
            self._stop_llama_server()
            time.sleep(1.0)

        port = self._pick_port(prefer_port)
        if port is None:
            error_msg = (
                f"Не удалось подобрать свободный порт для llama-server "
                f"(все {LLAMA_SERVER_PORT}–{LLAMA_SERVER_PORT + 49} заняты). "
                f"Закройте процесс, занимающий {LLAMA_SERVER_PORT}–{LLAMA_SERVER_PORT + 9}, "
                f"или выберите порт вручную в настройках."
            )
            self.last_error = error_msg
            _llama_last_error = error_msg
            if progress_callback:
                progress_callback(f"Ошибка: {error_msg}")
            return False

        self.base_url = f"http://127.0.0.1:{port}"

        cmd = [
            str(server_exe),
            "-m", str(LLM_MODEL_PATH),
            "-c", str(LLM_CONTEXT),
            "--host", "127.0.0.1",
            "--port", str(port),
        ]
        if self._use_gpu_setting() and self.cuda_build_present():
            # GPU-режим (CUDA-сборка, -ngl 99): кадры считает видеокарта, а
            # потоки llama.cpp расходуются в основном на sampling + mmap-чтение
            # страниц весов, которые «добиваются» на CPU. Ограничиваем их двумя —
            # CPU падает почти вдвое при практически той же tok/s (генерация
            # упирается в GPU, а не в процессор).
            cmd += ["-ngl", str(LLAMA_GPU_LAYERS)]
            cmd += ["--flash-attn", "on"]
            llama_threads = "2"
        else:
            # Явный -ngl 0: с установленной CUDA-сборкой llama.cpp может сам
            # решить выгрузить модель на GPU, что противоречит «Использовать GPU»=выкл.
            cmd += ["-ngl", "0"]
            # CPU-режим: модель целиком на процессоре, поэтому потоки нужны
            # все (эвлюация и промпт идут на CPU).
            llama_threads = str(max(1, os.cpu_count() or 4))
        cmd += ["--threads", llama_threads]

        # Лог llama-server пишем в файл: он нужен и для сводки offload
        # (задача 1), и для диагностики крашей (раньше всё уходило в
        # DEVNULL — причина провала была невидима).
        llama_log_path = LLAMA_DIR / "llama-server.log"
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(llama_log_path, "w", encoding="utf-8", errors="replace") as llama_log:
                proc = subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                    stdout=llama_log,
                    stderr=subprocess.STDOUT,
                )
        except OSError as exc:
            error_msg = f"Не удалось запустить llama-server: {exc}"
            self.last_error = error_msg
            _llama_last_error = error_msg
            return False

        if progress_callback:
            progress_callback("Ожидание запуска llama-server...")
        for _ in range(60):
            # Процесс умер сразу (нет DLL, занят порт и т.д.) — не ждём
            # 2 минуты молча, а сразу отдаём хвост лога в ошибку.
            if proc.poll() is not None:
                error_msg = (
                    f"llama-server завершился (код {proc.returncode}) — "
                    f"хвост лога: {self._llama_log_tail(llama_log_path)}"
                )
                self.last_error = error_msg
                _llama_last_error = error_msg
                if progress_callback:
                    progress_callback(f"Ошибка: {error_msg}")
                return False
            try:
                r = requests.get(f"{self.base_url}/health", timeout=2)
                if r.status_code == 200:
                    _llama_actual_port = port
                    _manage_llama_pid(proc.pid)
                    _touch_llama()
                    _ensure_llama_idle_watcher()
                    if not self._offload_summary_logged:
                        self._log_offload_summary(str(llama_log_path))
                        self._offload_summary_logged = True
                    return True
            except requests.RequestException:
                pass
            time.sleep(2)
        self.last_error = f"llama-server на порту {port} не ответил на /health за 2 мин."
        _llama_last_error = self.last_error
        return False

    def start_server(self, progress_callback=None, port: int | None = None) -> bool:
        """Пытается поднять llama-server (ручной перезапуск, возможно с другим портом).

        port=None → авто-подбор первого свободного.
        """
        global _llama_last_error
        server_exe = self._find_server_exe()
        if server_exe is None:
            self.last_error = "llama-server.exe не найден — запустите «Установить LLM»."
            _llama_last_error = self.last_error
            return False
        if not LLM_MODEL_PATH.exists():
            self.last_error = f"Модель не найдена: {LLM_MODEL_PATH}"
            _llama_last_error = self.last_error
            return False
        return self._start_server(server_exe, progress_callback, prefer_port=port)

    @staticmethod
    def _llama_log_tail(path: Path, limit: int = 3) -> str:
        """Последние непустые строки лога llama-server для сообщения об ошибке."""
        try:
            lines = [
                ln.strip()
                for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip()
            ]
        except OSError:
            return "лог недоступен"
        if not lines:
            return "лог пуст"
        return " | ".join(lines[-limit:])

    def _log_offload_summary(self, llama_log) -> None:
        """Собирает из лога llama-server сводку offload/GPGPU и пишет её
        в лог приложения (сводка до первого успешного /health).

        Новые сборки llama.cpp не печатают «offloaded N/M layers», поэтому
        факты о режиме берём из cmdline живого процесса (-ngl, flash-attn),
        а из лога — модель и треды. Безопасен: если лог ещё не успел
        записаться, просто молча пропускаем.
        """
        try:
            if isinstance(llama_log, (str, os.PathLike)):
                path = Path(llama_log)
            elif hasattr(llama_log, "name"):
                path = Path(llama_log.name)
            else:
                path = None
        except (TypeError, ValueError):
            path = None
        lines: list[str] = []
        try:
            if path is not None and path.exists():
                txt = path.read_text(encoding="utf-8", errors="replace")
                lines = txt.splitlines()
            elif hasattr(llama_log, "read"):
                llama_log.flush()
                llama_log.seek(0)
                txt = llama_log.read().decode("utf-8", "replace")
                lines = txt.splitlines()
        except (OSError, AttributeError, UnicodeDecodeError):
            return

        hits: dict[str, list[str]] = {
            "layers": [],
            "flash_attn": [],
            "threads": [],
            "model": [],
        }
        for ln in lines[-400:]:
            low = ln.lower()
            is_layers = "offloaded" in low and "layers to gpu" in low
            is_offload = "offload" in low and ("cpu" in low or "gpu" in low)
            if is_layers or is_offload:
                hits["layers"].append(ln.strip())
            elif "flash_attn" in low and ("on" in low or "true" in low or "=1" in low):
                hits["flash_attn"].append(ln.strip())
            elif "threads" in low or "n_threads" in low:
                hits["threads"].append(ln.strip())
            elif "model size" in low:
                hits["model"].append(ln.strip())
            elif "loading model" in low:
                m = re.search(r"loading model\s+'([^']+)'", ln, re.IGNORECASE)
                model = m.group(1) if m else ln.strip()
                hits["model"].append(model.replace("\\", "/").split("/")[-1])

        summary: list[str] = []
        # Режим из cmdline живого процесса — главный факт (новые сборки
        # llama.cpp строчки «offloaded N layers» в лог не пишут).
        cmdline = _llama_cmdline()
        m = re.search(r"-ngl\s+(\d+)", cmdline)
        if m:
            ngl = int(m.group(1))
            summary.append(f"{'gpu' if ngl > 0 else 'cpu'} (ngl={ngl})")
        m = re.search(r"--flash-attn\s+(\w+)", cmdline)
        if m:
            summary.append(f"flash-attn: {m.group(1)}")
        if hits["layers"]:
            summary.append(hits["layers"][-1])
        if hits["model"]:
            summary.append("model: " + hits["model"][-1])
        if hits["flash_attn"]:
            summary.append("flash-attn: " + hits["flash_attn"][-1])
        if hits["threads"]:
            summary.append("threads: " + hits["threads"][-1])
        if summary:
            text = "Llama offload: " + " | ".join(summary)
            self._offload_summary = text
            get_logger().info(text)

    def _find_server_exe(self):
        """Ищет llama-server.exe в LLAMA_DIR (или подпапках)."""
        server_exe = LLAMA_DIR / "llama-server.exe"
        if server_exe.exists():
            return server_exe
        for candidate in LLAMA_DIR.rglob("llama-server.exe"):
            return candidate
        return None

    def download_model(
        self,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: Event | None = None,
    ) -> Path:
        """Скачивает GGUF-модель по config.GGUF_URL в models/llm/. Возвращает путь.

        on_progress(done_bytes, total_bytes) — прогресс скачивания
        (total_bytes=None, если сервер не прислал Content-Length).
        cancel — событие отмены; при установке скачивание прерывается.
        """
        if not GGUF_URL:
            raise RuntimeError("URL модели ИИ (GGUF_URL) не настроен — модель недоступна")
        from core.downloader import fetch_file

        llm_dir = LLM_MODEL_PATH.parent
        llm_dir.mkdir(parents=True, exist_ok=True)
        if LLM_MODEL_PATH.exists():
            return LLM_MODEL_PATH
        fetch_file(GGUF_URL, LLM_MODEL_PATH, on_progress=on_progress, cancel=cancel)
        return LLM_MODEL_PATH


def create_enhancer(engine: str | None = None) -> BaseEnhancer:
    """Фабрика движков. Всегда llama.cpp (единственный активный движок)."""
    return LlamaCppEnhancer()
