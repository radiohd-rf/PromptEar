# Миграция улучшения текста на llama.cpp + сравнение моделей — Spec v0.1

## Цель

Заменить Ollama (qwen2.5:3b) на llama.cpp (`llama-server.exe`, OpenAI-совместимый API) с моделью gemma-4-E2B-it, перевести кэши моделей внутрь портативной папки. Отдельно подключить SAGE-1.7B как альтернативный движок исправления русского текста и сравнить оба на живой транскрипции — победитель становится движком по умолчанию. Фокус: **CPU-сборка**.

---

## 1. Кандидаты моделей

| | **gemma-4-E2B-it** | **SAGE-1.7B** |
|---|---|---|
| Архитектура | decoder-only (gguf, как Qwen) | T5 / FRED-T5-1.7B (encoder-decoder) |
| Файл | `gemma-4-E2B-it-UD-Q4_K_XL.gguf` | `ai-forever/sage-v1.1.0` → `model.safetensors` |
| Размер | 3.18 ГБ | 6.96 ГБ (safetensors) |
| Рантайм | llama.cpp `llama-server` | Python Transformers (CPU) |
| API | `/v1/chat/completions` (OpenAI) | in-process вызinfer |
| Роль | 3-проходный редактор (промпты сохраняются) | однопроходный корректор (орфография + пунктуация + заглавные), без промптов |
| Статус | **есть на ПК** (`G:\models\e2b\`) | скачивать с HF |

Примечание: сделать оба через llama-server нельзя — T5/FRED-T5 не поддерживается `llama-server` (работает только в `llama-cli`, PR #8141; серверная поддержка не доделана). Поэтому SAGE живёт в отдельном Python-процессе.

---

## 2. `config.py` — новые настройки

```python
# ── LLM-движок ───────────────────────────────────────────
LLM_ENGINE = "llama"            # "llama" | "sage"
LLM_MODEL_PATH = Path(BASE_DIR) / "models" / "llm" / "gemma-4-E2B-it-UD-Q4_K_XL.gguf"
LLM_BASE_URL = "http://127.0.0.1:8080"   # llama-server
LLM_MODEL = "gemma-4-E2B-it"             # только для отображения
LLM_TIMEOUT = 600                        # фикс бага #15: 300 → 600
LLM_TEMPERATURE = 0.0
LLM_NUM_PREDICT = -1
LLM_CONTEXT = 4096                       # -c для llama-server

# ── Кэш внутри портативной папки ─────────────────────────
HF_HOME = Path(BASE_DIR) / "models" / "hf"        # faster-whisper / transformers / sage
CT2_CACHE = Path(BASE_DIR) / "models" / "ct2"      # ctranslate2 (whisper)

# ── SAGE ────────────────────────────────────────────────
SAGE_MODEL_DIR = Path(BASE_DIR) / "models" / "sage"   # ai-forever/sage-v1.1.0
SAGE_MAX_CHARS = 1000   # FRED-T5 вход ≤512 токенов → чанк меньше LLM-чанка
```

Старые `OLLAMA_*` удаляются. `ENHANCER_CHUNK_SIZE = 3000` остаётся (для LLM-движка).

При запуске приложения: если `HF_HOME` / `CT2_CACHE` не заданы через переменные окружения — задать через `os.environ.setdefault` до импорта faster-whisper/transformers.

---

## 3. `processing/enhancer.py` — рефакторинг

Ввести общий интерфейс (ABC), сохранив существующие методы использования:

```python
class BaseEnhancer(ABC):
    def is_available(self) -> tuple[bool, bool]: ...
    def enhance_multi_pass(self, text, topic="", progress_callback=None, cancel=None) -> str: ...
    def install(self, progress_callback=None) -> bool: ...
    def get_engine_name(self) -> str: ...
```

### 3.1 `LlamaCppEnhancer` (замена `OllamaEnhancer`)
- `is_available()`: не через `ollama list`, а запрос `GET /health` llama-server + проверка существования GGUF-файла. Возвращает `(server_ok, model_ok)`.
- `_call_llm(prompt, timeout)`: `POST {LLM_BASE_URL}/v1/chat/completions`, body `{"messages":[{"role":"user","content":prompt}], "temperature":0, "max_tokens":-1}`. Ответ: `r.json()["choices"][0]["message"]["content"]`.
- 3 прохода и промпты (`_pass_cleanup/_pass_style/_pass_structure`), `_chunk_text`, `_protect_speakers/_restore_speakers`, `_result_too_short` — **переносятся без изменений**.
- `install()`: скачивает и распаковывает llama.cpp Windows build + запускает сервер (см. `core/installers.py`), ждёт `/health`.

### 3.2 `SageEnhancer`
- `is_available()`: импорт `transformers` + наличие `SAGE_MODEL_DIR/model.safetensors`.
- `enhance_multi_pass()`: однопроходный, без промптов. Каждый чанк (`_chunk_text(text, SAGE_MAX_CHARS)`) прогоняется через T5:
  `model(input_ids, attention_mask, decoder_input_ids)` → correction. Один чанк — один forward (без генерации по одному токену на проход/чанк, т.к. это seq2seq-корректор).
- Спикеры `[СПИКЕР N]:` не используются в промптах — SAGE правит сам текст; `_protect_speakers` **не** применяется (это LLM-трюк для промптов).
- Лишний результат усекается до исходной длины чанка как защита (фикс: ratio из `_result_too_short` применим).
- `install()`: скачивает `ai-forever/sage-v1.1.0` в `SAGE_MODEL_DIR` (huggingface-cli / `snapshot_download`).

### 3.3 Фабрика
```python
def create_enhancer(engine: str | None = None) -> BaseEnhancer:
    engine = engine or LLM_ENGINE
    return SageEnhancer() if engine == "sage" else LlamaCppEnhancer()
```

---

## 4. `core/installers.py`

**`OllamaInstaller` → `LlamaCppInstaller`**:
- `check(enhancer, emit, install_callback)` — асинхронная проверка `enhancer.is_available()`, эмитит `LlmReadyEvent`.
- `install(enhancer, emit)` — фоновый поток:
  1. Скачать `llama-b<ver>-bin-win-cpu-x64.zip` (CPU-only) с `https://github.com/ggml-org/llama.cpp/releases`
  2. Распаковать в `<BASE_DIR>/llama/`: `llama-server.exe` + `ggml.dll`
  3. Запустить: `llama-server.exe -m <LLM_MODEL_PATH> -c <LLM_CONTEXT> --host 127.0.0.1 --port 8080 --threads 4`
  4. Ждать `GET /health` (poll c таймаутом, как wait_ollama)
  5. Всё выполняется с `CREATE_NO_WINDOW` (без консольных окон)
- Для **SAGE** — отдельный `SageInstaller` (скачивание HF-модели в фоне с прогрессом).

Эмитится `LlmReadyEvent` (см. §5); на ошибку — `ErrorEvent`/`LogEvent` как раньше.

---

## 5. `core/events.py`, `core/__init__.py`, `utils/protocol.py`

- `QueueMsg.OLLAMA_READY` → `QueueMsg.LLM_READY`
- `OllamaReadyEvent(ollama_ok, model_ok)` → `LlmReadyEvent(server_ok, model_ok)` (поля процесса различаются для llama/sage; пока оставить `(ok, model_ok)` семантически identical)
- Экспорты в `core/__init__.py` и `utils/protocol.py` обновить.

---

## 6. `web/server.py` — SSE и API

- `_event_to_dict`: `case LlmReadyEvent(): {"type": "llm_ready", "llm_ok": ..., "model_ok": ...}`
- `_process_files`: `enhancer = create_enhancer()`; эмит `LlmReadyEvent`; `enhancer=enhancer if ok and model_ok else None`. Убрать «qwen_available» жёстко `True` → `config.qwen_available = ok and model_ok` (текущий `True` — потенциальный баг, pipeline гасит улучшение только флагом).
- `/api/ollama` → `/api/llm` (и все ссылки внутри).

---

## 7. `web/index.html`, `web/script.js`

- `#ollama-status` → `#llm-status`, подпись `Qwen: ...` → `LLM: <engine>`.
- `case 'ollama_ready'` → `case 'llm_ready'`; текст: «⚠ LLM-сервер не найден. Улучшение отключено.» / «⚠ Модель не найдена» / «✅ LLM доступен (gemma-4-E2B / SAGE)».
- `fetch('/api/ollama')` → `fetch('/api/llm')`, поля `ollama_ok` → `llm_ok`.

---

## 8. `bootstrap.bat` — шаг 6 (замена Ollama)

```
[6/6] Checking llama.cpp...
if exist "%APP_DIR%llama\llama-server.exe" → skip
иначе curl llama.cpp release zip (cpu) → unzip → копия llama-server.exe+ggml.dll
if not exist "%APP_DIR%models\llm\*.gguf" → плейсхолдер/подсказка куда положить модель
```
- Если движок SAGE: вместо llama.cpp — мелкая проверка `models\sage\` (модель скачивается на лету из приложения).
- При первом запуске приложение само докачивает недостающее через `LlmCppInstaller.install()` — bootstrap минимален (как с ffmpeg: встроено в сборку, плюс fallback).

---

## 9. `build_zips.py`

- + `LLAMA_RELEASE_URL` (win-cpu zip) → распаковать `llama-server.exe`/`ggml.dll` в корень build (как ffmpeg, **для CPU-сборки; в CUDA-сборку не включать**).
- + копирование `LLM_MODEL_PATH` (`G:\models\e2b\gemma-4-E2B-it-UD-Q4_K_XL.gguf`) в `build/models/llm/`. Если файла нет — не падать, а предупредить (модель скачается на лету).
- `REQUIRED_PACKAGES` не меняется для llama-пути (`requests` уже есть); `transformers` добавлять в сборку **только** если выбран SAGE.
- Итоговый CPU-размер: ~180 (база+ffmpeg) + 3.2 ГБ (GGUF) + ~40 МБ (llama-server).

---

## 10. Сравнение и выбор победителя

Перед фиксацией движка по умолчанию прогнать **один и тот же** тестовый аудиофайл (~2-3 мин, разговорная речь с повторами/ошибками распознавания):

1. gemma + 3-проходная схема (промпты)
2. SAGE-1.7B однопроходный корректор
3. (опционально) SAGE-1.7B + промпт-проходы 2-3 через llama для стиля/структуры

**Метрики:**
- Качество: орфография/пунктуация/заглавные (визуально + кол-во правок), отсутствие фантазий (сравнение с исходником)
- Минимальность изменений (длина результата/исходника, ratio 0.6–1.4)
- Скорость (мин на файл), потребление RAM
- Портативность (размер модели в сборке)

Результат сравнения — в заметку Obsidian + фиксируется `LLM_ENGINE` по умолчанию в `config.py`.

---

## 11. Поток (после миграции)

```
POST /api/files
  └─ enhancer = create_enhancer(LLM_ENGINE)
       ├─ llama → llama-server уже запущен (installers) → /v1/chat/completions
       └─ sage  → transformers T5 in-process, чанки ≤1000 симв.
  └─ pipeline: pass1→pass2→pass3 (llama)  ИЛИ  один прогон (sage)
  └─ события: llm_ready / busy / progress / done / error
```

---

## 12. Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| llama-server не запущен / GGUF нет | `LlmReadyEvent(False, ...)` → «Улучшение отключено», транскрибация продолжается |
| Таймаут сервера (>600с) | `LLM_TIMEOUT=600` + повтор запроса 1 раз (фикс бага #15) |
| SAGE: модель не скачана | `LlmReadyEvent(False, False)` + кнопка установки |
| SAGE: чанк > 512 токенов | предварительный `_chunk_text(text, SAGE_MAX_CHARS)` |
| Результат слишком короткий | `_result_too_short` → сохранить предыдущий чанк |

---

## 13. Версия

- v0.13.0 (после слияния локального master с origin/master, где v0.12.0)

---

## 14. Файлы для изменения

| Файл | Действие |
|------|----------|
| `config.py` | `OLLAMA_*` → `LLM_*`/`SAGE_*`, + пути кэша внутри папки, ~+15 строк |
| `processing/enhancer.py` | рефакторинг: `BaseEnhancer`, `LlamaCppEnhancer`, `SageEnhancer`, `create_enhancer` |
| `core/installers.py` | `OllamaInstaller` → `LlamaCppInstaller` + `SageInstaller` |
| `core/events.py` | `OllamaReadyEvent` → `LlmReadyEvent`, `QueueMsg.LLM_READY` |
| `core/__init__.py` | экспорт `LlmReadyEvent` |
| `utils/protocol.py` | переименование энама |
| `web/server.py` | SSE-маппинг, `/api/llm`, `create_enhancer`, убрать хардкод `qwen_available=True` |
| `web/index.html`, `web/script.js` | `ollama_*` → `llm_*`, текст статусов |
| `bootstrap.bat` | шаг 6: Ollama → llama.cpp (или SAGE) |
| `build_zips.py` | + llama-server/ggml.dll, + GGUF в CPU-сборку |
| `requirements.txt` | + `transformers` (только если выбран SAGE) |
| `uninstall.bat` | строка «4 — Модель Qwen 2.5 3b» → актуальный движок |