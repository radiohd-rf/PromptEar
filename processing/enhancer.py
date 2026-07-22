"""Улучшение текста через Ollama + Qwen. 3-проходная система."""

import os
import re
import subprocess
import tempfile
import time

import requests

from config import (
    ENHANCER_CHUNK_SIZE,
    MULTI_PASS_MAX_RATIO,
    MULTI_PASS_MIN_RATIO,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_NUM_PREDICT,
    OLLAMA_TEMPERATURE,
    OLLAMA_TIMEOUT,
)
from processing.topic import detect_topic

PASS_LABELS = {
    "pass1": "Очистка (орфография, пунктуация, повторы)",
    "pass2": "Стиль (грамматика, согласование)",
    "pass3": "Структура (абзацы, диалоги)",
}


class OllamaEnhancer:
    """Класс для улучшения текста через Ollama + Qwen 2.5:3b."""

    def __init__(self, model: str = OLLAMA_MODEL):
        self.model = model
        self.base_url = OLLAMA_BASE_URL
        self._ollama_ok = False
        self._model_ok = False
        self.topic = ""
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def is_available(self) -> tuple[bool, bool]:
        """Проверяет, установлена ли Ollama и скачана ли модель.

        Возвращает (ollama_installed, model_downloaded).
        """
        try:
            r = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode != 0:
                return False, False
        except FileNotFoundError:
            return False, False

        try:
            r = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True, self.model in r.stdout
        except Exception:
            return True, False

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

        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": OLLAMA_TEMPERATURE,
            "stream": False,
        }
        r = self._get_session().post(
            f"{self.base_url}/api/generate",
            json=payload,  # type: ignore[arg-type]
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["response"].strip()

    # ── 3-проходная система ───────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str, max_size: int) -> list[str]:
        """Режет текст на чанки по границам предложений, каждый ≤ max_size символов."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks, current, curr_len = [], [], 0
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

    def enhance_multi_pass(self, text: str, topic: str = "", progress_callback=None) -> str:
        """3-проходное улучшение: очистка → стиль → структура.

        Длинные тексты дробятся на чанки, каждый обрабатывается независимо.
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
            chunk = self._protect_speakers(chunk)

            for name, pass_fn in passes:
                label = f"Проход {name[4:]}/3: {PASS_LABELS.get(name, name)}"
                if len(chunks) > 1:
                    label = f"Чанк {idx + 1}/{len(chunks)}: {label}"
                if progress_callback:
                    progress_callback(label)
                try:
                    result = pass_fn(chunk, topic)
                    if self._result_too_short(result, chunk):
                        if progress_callback:
                            progress_callback(
                                "  Результат слишком короткий (<60% длины), сохранён предыдущий"
                            )
                    else:
                        chunk = result
                except Exception:
                    if progress_callback:
                        progress_callback(f"  Ошибка на проходе {name[4:]}, сохранён предыдущий")

            processed.append(self._restore_speakers(chunk))

        return "\n\n".join(processed)

    # ── Отдельные проходы ──────────────────────────────────────────────────

    def _call_ollama(self, prompt: str, timeout: int = OLLAMA_TIMEOUT) -> str:
        """Отправляет запрос в Ollama и возвращает ответ."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": OLLAMA_NUM_PREDICT,
            "stream": False,
        }
        r = self._get_session().post(
            f"{self.base_url}/api/generate",
            json=payload,  # type: ignore[arg-type]
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["response"].strip()

    def _pass_cleanup(self, text: str, topic: str) -> str:
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
        return self._call_ollama(prompt)

    def _pass_style(self, text: str, topic: str) -> str:
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
        return self._call_ollama(prompt)

    def _pass_structure(self, text: str, topic: str) -> str:
        """Проход 3: разбивка на абзацы, оформление диалогов."""
        prompt = (
            "Ты — редактор. Вход — текст с корректной орфографией и грамматикой.\n\n"
            "Что можно делать:\n"
            "- Разбить на абзацы по смене темы или говорящего (добавить пустые строки)\n"
            "- Если есть диалоги/прямая речь — каждый реплика с новой строки\n"
            "- Объединить короткие однострочные абзацы в связные блоки\n\n"
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
        return self._call_ollama(prompt)

    @staticmethod
    def _result_too_short(result: str, original: str) -> bool:
        """Проверяет, не упростил ли Qwen текст сильнее допустимого.

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

        Чтобы Qwen не удалял реплики, превращаем «— текст» в «[СПИКЕР 1]: текст».
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
        """Скачивает и устанавливает Ollama, затем скачивает модель.

        Возвращает True при успехе.
        """
        if progress_callback:
            progress_callback("Скачивание Ollama...")

        url = "https://ollama.com/download/OllamaSetup.exe"
        setup_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(f"  Попытка {attempt}/{max_retries}...")
                r = requests.get(url, stream=True, timeout=(15, 120))
                r.raise_for_status()
                with open(setup_path, "wb") as f:
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
            progress_callback("Установка Ollama...")
        subprocess.run([setup_path, "/S"], check=True, timeout=300)

        if progress_callback:
            progress_callback("Ожидание запуска Ollama...")
        for _ in range(30):
            check = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if check.returncode == 0:
                break
            time.sleep(2)

        if progress_callback:
            progress_callback(f"Скачивание модели {self.model}...")
        subprocess.run(
            ["ollama", "pull", self.model],
            check=True,
            timeout=600,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        return True
