"""Улучшение текста через LLM-движок: llama.cpp (llama-server) или SAGE (T5).

Единый интерфейс BaseEnhancer: llama-движок сохраняет 3-проходную систему,
SAGE — однопроходный корректор орфографии/пунктуации/заглавных.
"""

import json
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from threading import Event

import requests

from config import (
    ENHANCER_CHUNK_SIZE,
    LLAMA_DIR,
    LLAMA_RELEASE,
    LLAMA_SERVER_PORT,
    LLM_BASE_URL,
    LLM_CONTEXT,
    LLM_ENGINE,
    LLM_MODEL,
    LLM_MODEL_PATH,
    LLM_NUM_PREDICT,
    LLM_RETRIES,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    MULTI_PASS_MAX_RATIO,
    MULTI_PASS_MIN_RATIO,
    SAGE_HF_REPO,
    SAGE_MAX_CHARS,
    SAGE_MODEL_DIR,
)
from processing.topic import detect_topic

PASS_LABELS = {
    "pass1": "Очистка (орфография, пунктуация, повторы)",
    "pass2": "Стиль (грамматика, согласование)",
    "pass3": "Структура (абзацы, диалоги, без воды)",
}


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
    ) -> str:
        """Улучшает текст (полный режим). stream_callback(text, pass_no, chunk_no, chunk_total)."""

    @abstractmethod
    def install(self, progress_callback=None) -> bool:
        """Устанавливает движок и модель. True при успехе."""

    @abstractmethod
    def get_engine_name(self) -> str:
        """Имя движка: 'llama' | 'sage'."""


class LlamaCppEnhancer(BaseEnhancer):
    """Улучшение текста через llama-server (OpenAI-совместимый API) + gemma-4-E2B."""

    def __init__(self, model: str = LLM_MODEL):
        self.model = model
        self.base_url = LLM_BASE_URL
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def get_engine_name(self) -> str:
        return "llama"

    def is_available(self) -> tuple[bool, bool]:
        """Проверяет запущен ли llama-server и есть ли GGUF-файл.

        Возвращает (server_ok, model_ok).
        """
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            server_ok = r.status_code == 200
        except requests.RequestException:
            server_ok = False
        if not server_ok:
            return False, False
        return True, LLM_MODEL_PATH.exists()

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

    def enhance_multi_pass(
        self,
        text: str,
        topic: str = "",
        progress_callback=None,
        cancel: Event | None = None,
        stream_callback=None,
    ) -> str:
        """3-проходное улучшение: очистка → стиль → структура.

        Длинные тексты дробятся на чанки, каждый обрабатывается независимо.
        stream_callback(text_so_far, pass_no, chunk_no, chunk_total) вызывается
        по мере генерации каждого прохода («модель печатает»).
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
                        )
                    except Exception:
                        if progress_callback:
                            progress_callback(
                                f"  Ошибка на проходе {name[4:]}, сохранён предыдущий"
                            )
                        result = chunk
                else:
                    try:
                        result = pass_fn(chunk, topic)
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
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_NUM_PREDICT,
            "stream": False,
        }
        url = f"{self.base_url}/v1/chat/completions"
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
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_NUM_PREDICT,
            "stream": True,
        }
        url = f"{self.base_url}/v1/chat/completions"
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
        """
        lines = text.split("\n")
        speaker_count = 0
        result = []
        for line in lines:
            stripped = line.strip()
            m = re.match(r"^[—–-]\s+(.+)", stripped)
            if m:
                speaker_count += 1
                result.append(f"[СПИКЕР {speaker_count}]: {m.group(1)}")
            else:
                result.append(line)
        return "\n".join(result)

    @staticmethod
    def _restore_speakers(text: str) -> str:
        """Обратное преобразование: [СПИКЕР N]: → —."""
        return re.sub(r"\[СПИКЕР \d+\]:\s?", "— ", text)

    # ── Установка ──────────────────────────────────────────────────────────

    def install(self, progress_callback=None) -> bool:
        """Скачивает llama.cpp (win-cpu), распаковывает и запускает llama-server.

        Возвращает True при успехе.
        """
        if progress_callback:
            progress_callback("Скачивание llama.cpp...")

        url = (
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-bin-win-cpu-x64.zip"
        )
        zip_path = LLAMA_DIR / "llama.zip"
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(f"  Попытка {attempt}/{max_retries}...")
                r = requests.get(url, stream=True, timeout=(15, 120))
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                break
            except requests.exceptions.ConnectionError:
                if attempt == max_retries:
                    raise
                if progress_callback:
                    progress_callback("  Сетевая ошибка, повтор через 3 сек...")
                time.sleep(3)

        if progress_callback:
            progress_callback("Распаковка llama.cpp...")
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(LLAMA_DIR)
        zip_path.unlink()

        server_exe = LLAMA_DIR / "llama-server.exe"
        if not server_exe.exists():
            for candidate in LLAMA_DIR.rglob("llama-server.exe"):
                server_exe = candidate
                break
        if not server_exe.exists():
            raise RuntimeError("llama-server.exe не найден в архиве llama.cpp")

        if not LLM_MODEL_PATH.exists():
            raise RuntimeError(
                f"Модель не найдена: {LLM_MODEL_PATH}. Положите GGUF в папку models/llm/"
            )

        return self._start_server(server_exe, progress_callback)

    def _start_server(self, server_exe, progress_callback=None) -> bool:
        """Запускает llama-server и ждёт /health."""
        if self.is_available()[0]:
            return True

        cmd = [
            str(server_exe),
            "-m", str(LLM_MODEL_PATH),
            "-c", str(LLM_CONTEXT),
            "--host", "127.0.0.1",
            "--port", str(LLAMA_SERVER_PORT),
            "--threads", str(max(1, os.cpu_count() or 4)),
        ]
        subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if progress_callback:
            progress_callback("Ожидание запуска llama-server...")
        for _ in range(60):
            try:
                r = requests.get(f"{LLM_BASE_URL}/health", timeout=2)
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(2)
        return False


class SageEnhancer(BaseEnhancer):
    """Однопроходный корректор русского текста на базе FRED-T5-1.7B (SAGE).

    Исправляет орфографию, пунктуацию и заглавные буквы. Без промптов.
    """

    def __init__(self, model_dir: os.PathLike | str = SAGE_MODEL_DIR):
        self.model_dir = os.fspath(model_dir)
        self._tokenizer = None
        self._model = None

    def get_engine_name(self) -> str:
        return "sage"

    def is_available(self) -> tuple[bool, bool]:
        """Проверяет наличие transformers и скачанной модели."""
        import importlib.util

        has_tf = importlib.util.find_spec("transformers") is not None
        model_file = SAGE_MODEL_DIR / "model.safetensors"
        return has_tf, model_file.exists()

    def _load(self):
        """Загружает токенизатор и модель (лениво, один раз)."""
        if self._model is not None:
            return
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_dir)

    def enhance_multi_pass(
        self,
        text: str,
        topic: str = "",
        progress_callback=None,
        cancel: Event | None = None,
        stream_callback=None,
    ) -> str:
        """Однопроходная коррекция. Чанки ≤ SAGE_MAX_CHARS (вход T5 ≤512 токенов)."""
        if not text.strip():
            return text

        self._load()
        assert self._tokenizer is not None and self._model is not None

        chunks = LlamaCppEnhancer._chunk_text(text, SAGE_MAX_CHARS)
        processed = []
        for idx, chunk in enumerate(chunks):
            if cancel is not None and cancel.is_set():
                break
            if progress_callback:
                label = f"SAGE: чанк {idx + 1}/{len(chunks)}"
                progress_callback(label)
            if stream_callback is not None:
                stream_callback(chunk, 1, idx + 1, len(chunks))
            try:
                corrected = self._correct_chunk(chunk)
                if not corrected or len(corrected) < len(chunk) * MULTI_PASS_MIN_RATIO:
                    corrected = chunk
            except Exception:
                corrected = chunk
            if stream_callback is not None:
                stream_callback(corrected, 1, idx + 1, len(chunks))
            processed.append(corrected)

        return "\n\n".join(processed)

    def _correct_chunk(self, chunk: str) -> str:
        assert self._tokenizer is not None and self._model is not None
        inputs = self._tokenizer(chunk, return_tensors="pt", truncation=True, max_length=512)
        outputs = self._model.generate(
            **inputs,
            max_new_tokens=512,
            temperature=0.0,
        )
        return self._tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

    def install(self, progress_callback=None) -> bool:
        """Скачивает ai-forever/sage-v1.1.0 в папку models/sage."""
        if progress_callback:
            progress_callback(f"Скачивание модели {SAGE_HF_REPO}...")
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise RuntimeError(
                "huggingface_hub не установлен. Установите: pip install huggingface_hub"
            ) from None

        SAGE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=SAGE_HF_REPO,
            local_dir=SAGE_MODEL_DIR,
            allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model"],
        )
        return self.is_available()[1]


def create_enhancer(engine: str | None = None) -> BaseEnhancer:
    """Фабрика движков по конфигу."""
    engine = engine or LLM_ENGINE
    if engine == "sage":
        return SageEnhancer()
    return LlamaCppEnhancer()
