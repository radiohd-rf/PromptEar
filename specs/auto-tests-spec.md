# Спецификация автотестов PromptEar

**Версия:** 1.0
**Статус:** Черновик

---

## 1. Цель

Обеспечить регрессионную защиту ключевых модулей:
- `processing/` — transcriber, enhancer, detector, topic
- `utils/` — files, gpu, protocol
- `config.py`
- Интеграция: полный pipeline на реальном аудио (10 сек, без GUI)

---

## 2. Тестовый раннер

- **pytest** (марки: `unit`, `smoke`, `gui`)
- Плагины: `pytest-mock` (встроенный `mocker`), `requests-mock`

---

## 3. Структура

```
tests/
├── conftest.py                 # Глобальные фикстуры
├── mocks/
│   └── whisper_model.py        # Mock whisper.load_model
├── unit/
│   ├── test_config.py          # Типы констант
│   ├── test_protocol.py        # QueueMsg enum
│   ├── test_files.py           # find_audio_files, save_txt, save_docx
│   ├── test_gpu.py             # GPU detection (mocked)
│   ├── test_detector.py        # ffmpeg mock, detect_quiet_audio
│   ├── test_topic.py           # topic keyword scoring
│   ├── test_enhancer.py        # 1-pass + 3-pass + fallback + speaker protection
│   ├── test_transcriber.py     # Whisper mock, progress parsing
│   └── test_queue.py           # QueueMsg types, _check_queue routing
├── integration/
│   └── test_real_transcribe.py # 10-sec WAV → Whisper → Qwen (если доступны)
├── data/
│   └── test_10s.wav            # Тестовый аудиофайл (~10 сек)
├── pytest.ini
```

---

## 4. Стратегия моков

| Модуль | Что мокаем | Инструмент |
|--------|-----------|------------|
| `transcriber.py` | `whisper.load_model()`, `model.transcribe()` | `unittest.mock.patch` |
| `enhancer.py` | `requests.post()` | `requests_mock` |
| `detector.py` | `subprocess.run()` | `mocker.patch` |
| `gpu.py` | `subprocess.run()`, `torch.cuda.is_available()` | `mocker.patch` |
| `files.py` | `Path.write_text()`, `docx.Document()` | `mocker.patch` / `tmp_path` |

---

## 5. Тест-кейсы

### 5.1 `test_config.py`

| Кейс | Проверка |
|------|----------|
| `test_colors_are_hex` | Все `COLORS.values()` — `#xxx` или `#xxxxxx` |
| `test_ollama_url_valid` | `OLLAMA_BASE_URL` содержит `http://` |
| `test_temperature_range` | `OLLAMA_TEMPERATURE` в `[0.0, 1.0]` |
| `test_ratios_valid` | `MULTI_PASS_MIN_RATIO < MULTI_PASS_MAX_RATIO` |
| `test_audio_extensions_lowercase` | Все расширения строчные |

### 5.2 `test_protocol.py`

| Кейс | Проверка |
|------|----------|
| `test_all_types_unique` | Все `QueueMsg` имеют уникальные `auto()` значения |
| `test_all_types_have_handler` | Структурно: каждый тип обрабатывается в `_check_queue` |

### 5.3 `test_files.py`

| Кейс | Проверка |
|------|----------|
| `test_find_audio_files_dir` | Директория с `.mp3` + `.wav` → оба |
| `test_find_audio_files_single` | Один файл → возврат |
| `test_find_audio_files_empty` | Пустая директория → `[]` |
| `test_find_audio_files_unsupported` | `.txt` не включается |
| `test_save_txt` | Запись в `tmp_path` → сравнение |
| `test_save_docx` | Запись через mocked `Document` |

### 5.4 `test_gpu.py`

| Кейс | Проверка |
|------|----------|
| `test_has_nvidia_true` | `nvidia-smi` OK → `True` |
| `test_has_nvidia_false` | `nvidia-smi` не найден → `False` |
| `test_torch_cuda_installed` | `torch.version.cuda` не None |
| `test_torch_cuda_missing` | `torch.version.cuda` None |
| `test_detect_and_report` | `need_install` при GPU без CUDA |

### 5.5 `test_detector.py`

| Кейс | Проверка |
|------|----------|
| `test_detect_loud` | `mean_volume: -10.0 dB` → `False` |
| `test_detect_quiet` | `mean_volume: -30.0 dB` → `True` |
| `test_detect_error` | ffmpeg падает → `False` |
| `test_preprocess_ok` | Успешный ffmpeg → `Path` |
| `test_preprocess_fail` | ffmpeg ошибка → Exception |

### 5.6 `test_topic.py`

| Кейс | Проверка |
|------|----------|
| `test_history` | Ключевые слова истории → `история` |
| `test_education` | Ключевые слова образования → `образование` |
| `test_tech` | Ключевые слова техники → `техника` |
| `test_daily` | Без ключевых слов → `повседневный` |
| `test_empty` | Пустая строка → `повседневный` |

### 5.7 `test_enhancer.py`

| Кейс | Проверка |
|------|----------|
| `test_enhance_calls_ollama` | `requests.post` с правильным payload |
| `test_enhance_returns` | Ответ от Ollama → возврат строки |
| `test_enhance_error` | Ошибка запроса → пробрасывается |
| `test_multi_pass_three_calls` | 3 прохода = 3 вызова к Ollama |
| `test_multi_pass_fallback_pass1` | Pass1 упал → исходный |
| `test_multi_pass_fallback_pass2` | Pass2 упал → pass1 |
| `test_multi_pass_fallback_empty` | Пустой ответ → предыдущий |
| `test_multi_pass_too_short` | Короткий ответ (<60%) → fallback |
| `test_multi_pass_too_long` | Длинный ответ (>140%) → fallback |
| `test_protect_speakers` | `— текст` → `[СПИКЕР 1]: текст` |
| `test_restore_speakers` | `[СПИКЕР 1]: текст` → `— текст` |
| `test_result_too_short_boundary` | 50% → True, 70% → False |

### 5.8 `test_transcriber.py`

| Кейс | Проверка |
|------|----------|
| `test_transcribe_calls` | `model.transcribe` с правильным путём |
| `test_transcribe_returns` | Возвращает текст из результата |
| `test_progress_parsing` | Парсинг `50%|...| 5/10 [00:30<00:30, 5.0frames/s]` |
| `test_progress_queue` | `QueueMsg.WHISPER_PROGRESS` отправлен |
| `test_load_model_lazy` | Модель загружается один раз |

---

## 6. Интеграционный тест

Маркирован `@pytest.mark.smoke`. Единственный тест без моков:

1. Берёт `tests/data/test_10s.wav` (если нет → skip)
2. Загружает Whisper medium (если нет → skip)
3. Транскрибирует → результат не пустой
4. Если доступна Ollama → улучшает
5. Сохраняет в `tmp_path` → файл создан

---

## 7. Запуск

```bash
pytest tests/unit/ -v                # чистые unit
pytest tests/ -v                     # все
pytest -m smoke -v                   # только реальный транскрайб
pytest --cov=processing --cov=utils  # замер покрытия
```

---

## 8. Инструментарий

| Инструмент | Назначение |
|-----------|-----------|
| `pytest` | Раннер |
| `pytest-mock` | mock через `mocker` |
| `requests-mock` | mock HTTP |
| `pytest-cov` | покрытие |
| `tmp_path` | временные файлы |

---

## 9. Критерии готовности

- `pytest tests/unit/ -v` — **зелёный**
- `pytest tests/ -v` — зелёный при наличии WAV и Ollama
- Покрытие `processing/` + `utils/` ≥ **70%**
- Каждый модуль: минимум 1 успех + 1 ошибка
