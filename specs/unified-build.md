# Спека: единая версия PromptEar (CPU по умолчанию, модели по запросу)

Дата: 2026-09-22. Статус: согласовано, к реализации.

## 1. Цель

Заменить раздельные CPU/CUDA-сборки на **один универсальный дистрибутив**:

- В zip вшивается только самая лёгкая модель транскрибации (**tiny**, ~75 МБ).
- Gemma (GGUF, ~3 ГБ) **не** вшивается — скачивается в рантайме при первом включении ИИ.
- По умолчанию всё работает на CPU.
- Тяжёлые компоненты (модель Whisper побольше, Gemma, CUDA-компоненты) докачиваются
  из приложения с попапами-подтверждениями и прогресс-баром.
- Параметры обработки выбираются в **модале запуска** (формат, тайм-коды, ИИ, модель, GPU).

## 2. Текущее состояние (что мешает)

- `build_zips.py` собирает 2 варианта и вшивает GGUF 3 ГБ в CPU-вариант; Whisper
  (`models/ct2`) в сборку **не** входит.
- `core/installers.py`: `CudaInstaller`/`LlamaCppInstaller`/`SageInstaller` —
  фоновые pip/скачивания только с лог-прогрессом, без процентов.
- `processing/transcriber.py`: глобальная константа `config.WHISPER_MODEL`,
  устройство от `torch.cuda.is_available()`.
- `SETTINGS_FILE` (`config.py`) объявлен, нигде не используется.
- **Баг**: `/api/gpu` вызывается из `script.js:990`, но роута нет; в
  `web/server.py` после `return` в `translate_file` висит мёртвый код
  `info = detect_and_report(); return jsonify(info)` — остатки задуманного `/api/gpu`.

Решение по `SageEnhancer`: движок остаётся gemma (см. MEMORY), SAGE не развиваем,
`SageInstaller` не трогаем.

## 3. Catasлог моделей

### 3.1 Whisper (`core/whisper_models.py`, новый)

Алиас → метаданные:

| алиас | repo_id (HF) | размер | качество |
|---|---|---|---|
| `tiny` | `Systran/faster-whisper-tiny` | ~75 МБ | **дефолт, предустановлена**; слабо для ru |
| `base` | `Systran/faster-whisper-base` | ~145 МБ | компромисс |
| `small` | `Systran/faster-whisper-small` | ~488 МБ | |
| `medium` | `Systran/faster-whisper-medium` | ~1.5 ГБ | |
| `large-v3` | `Systran/faster-whisper-large-v3` | ~3 ГБ | |
| `large-v3-turbo` | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | ~1.5 ГиБ | текущая в dev |
| `distil-large-v3` | `Systran/faster-distil-whisper-large-v3` | ~750 МБ | быстрый, многоязычный |

- Внутри приложения модель хранится по `models/ct2/<repo_name>/`
  (скачивание через `download_root=models/ct2`).
- Каталог держит **одну** установленную модель: при смене скачиваем новую,
  затем **удаляем каталоги всех прочих версий** (до удаления —
  `Transcriber.unload()`, чтобы ctranslate2 не держал файлы).
- API модуля: `get_catalog()`, `resolve(alias) -> repo_id`, `size_mb(alias)`,
  `is_installed(alias)`, `installed_model()`, `remove_others(keep_alias)`.

### 3.2 Gemma (GGUF)

- В сборку не входит. Скачивается по `config.GGUF_URL` (HF-хостинг) в
  `models/llm/` через временный файл → `rename`.
  - **Открытый вопрос**: финальный URL — заменить плейсхолдер `GGUF_URL`
    до релиза. Файл: `gemma-4-E2B-it-UD-Q4_K_XL.gguf` (3.0 ГБ, источник dev-машины `G:\models\e2b\...`).
- `llama.cpp` (~25 МБ) в zip **вшивается** (дешевле, чем качать при активации ИИ).
- `config.LLM_MODEL_FILENAME` и `LLM_MODEL_PATH` остаются; рантайм пишет тот же файл.

## 4. Дистрибутив

`build_zips.py` → **один** вариант `PromptEar-<ver>.zip`:

- В zip: исходники, torch CPU wheels, остальные зависимости, `ffmpeg.exe`,
  каталог `llama/` (llama-server + DLL), `models/ct2/tiny/`.
- НЕ входит: GGUF, CUDA wheels, прочие модели Whisper.
- Ожидаемый вес: ~400–500 МБ.
- `VARIANTS`/вариантная логика удаляется; аргумент имени не нужен.
- `bootstrap.bat`: сообщения обновить — «модель транскрибации предустановлена,
  Gemma скачается при первом включении ИИ».

## 5. Настройки (`core/settings.py`, новый)

Поля в `settings.json` (`%APPDATA%/PromptEar/settings.json`):

```json
{
  "whisper_model": "tiny",
  "use_gpu": false,
  "output_format": "docx",
  "timestamps": false,
  "ai_enabled": false
}
```

- `load() -> dict` (с дефолтами), `save(patch)`.
- Endpoints: `GET /api/settings`, `POST /api/settings`.
- Используются как префилл модала запуска и состояния бэкенда.

## 6. Загрузчик (`core/downloader.py`, новый) + события

- `DownloadManager` — один активный download (гард в settings/памяти).
- Скачивание: `requests.get(stream=True)`, прогресс по `Content-Length`
  (нет длины → показываем «…»), временный файл `.part` → `rename`.
- Копирование уже локальной модели не нужно.

Новые события в `core/events.py`:

- `DownloadProgressEvent(download_id, label, pct, done_mb, total_mb|None, status)`
- `DownloadDoneEvent(download_id)`
- `DownloadFailedEvent(download_id, error)`

Endpoints:

- `POST /api/downloads` `{kind, model?}` → `{download_id}` (409, если занято).
  - `kind=whisper` (+`model`): скачать в `models/ct2`, удалить прочие,
    записать `whisper_model`.
  - `kind=gemma`: скачать GGUF в `models/llm`, затем `LlamaCppEnhancer.start_server()`.
  - `kind=cuda`: pip-swap torch/ctranslate2 на CUDA-сборку.
  - `kind=llama`: вшит в zip, endpoint на случай отсутствия.
- `GET /api/downloads/stream` — глобальный SSE-канал прогресса.
- `GET /api/downloads/status`.

Клиент открывает `EventSource('/api/downloads/stream')` при старте → прогресс-модал.

## 7. Флоу запуска (новый UI)

Раскладка без изменений: слева дропзона + список файлов + **поле контекста**,
справа лайв-окно. Из сайдбара **уходят** format/enhance/timestamps.

Кнопка «Запуск» → **модал параметров обработки**:

- **Формат** (DOCX/TXT/MD/SRT/VTT) — префилл из settings.
- **Тайм-коды [MM:SS]** (чекбокс).
- **Обработать с ИИ** (чекбокс):
  - gemma установлена → просто включает `auto`.
  - нет → попап «Скачать модель ИИ (~3 ГБ), это займёт время. Продолжить?» Да/Нет.
    - Да → прогресс-скачивание (модал), по завершении запуск.
    - Нет → чекбокс сброшен.
- **Модель транскрибации** (select; дефолт = установленная):
  - смена на не-установленную → попап «Скачать <модель> (~N МБ)? <текущая> будет
    удалена.» Да → скачивание (+удаление старой), по завершении apply; Нет → откат.
- **Использовать GPU** (disabled + подпись «GPU не обнаружен», если нет NVIDIA):
  - нет CUDA-компонентов → попап «Скачать CUDA-компоненты (~2.6 ГБ)?» Да → скачивание.
    - По завершении попап «Применить GPU? Значение сохранится. Перезапустить сейчас?»
      **Да** → `subprocess.Popen([sys.executable, main.py])` + `os._exit(0)`
      (значение уже в settings); **Нет** → работает дальше, GPU подхватится при
      следующем запуске приложения.
- **[Применить и запустить]** — сохраняет выбранные значения в `settings.json`
  (`POST /api/settings`) и сразу запускает обработку
  (`POST /api/files`); `enhance_mode` = `ai ? auto : none`.
- [Отмена] — закрывает модал без изменений.

Кнопка-шестерёнка «Настройки» в шапке → модал: префиллы + LLM-порт
(наследник текущей `llm-error-box`/`applyLlmPort`).

Прогресс-модал: бар + проценты + label, единый для всех видов скачиваний.

## 8. Backend / pipeline

- `/api/files` принимает `ai`, `whisper_model`, `use_gpu` (кроме текущих полей).
- `Transcriber.load_model`:
  - имя модели из `settings.whisper_model` (не константа `WHISPER_MODEL`);
  - `device = "cuda" if settings.use_gpu else "cpu"`,
    `compute_type = "float16" | "int8"`;
  - после успешного скачивания модель грузится `local_files_only=True`.
- `/api/gpu` — **починить**: роут из `web/server.py` (мёртвый код в `translate_file`
  превратить в полноценный endpoint, вернуть `detect_and_report()`).
- «Запуск» заблокирован, пока идёт скачивание whisper-модели или CUDA.
  Скачивание gemma может идти фоном (enhance уже умеет деградировать до черновика).
- «Перевести»/«Улучшить с ИИ»/skip/отмена — без изменений.

## 9. GPU-детекция без раннего импорта torch

- Заменить `torch_has_cuda()`-ветку в `utils/gpu.detect_and_report()` на
  `ctranslate2.get_cuda_device_count() > 0` + nvidia-smi; `torch` импортировать
  только внутри `load_model` (lazy).
- Зачем: при выборе «Нет» в попапе рестарта приложение должно подхватить CUDA
  без перезапуска процесса.

## 10. Изменяемые файлы

Новые:

- `core/settings.py`
- `core/downloader.py`
- `core/whisper_models.py`
- `specs/unified-build.md` (этот документ)

Изменяемые:

- `config.py` — `GGUF_URL` (плейсхолдер), `WHISPER_DEFAULT_MODEL="tiny"`.
- `core/events.py` — 3 события загрузки.
- `core/installers.py` — `GemmaInstaller`; `CudaInstaller` с прогрессом и
  попапом «Перезапустить?» (флаг результата, решение принимает UI).
- `processing/transcriber.py` — выбор модели из settings, lazy torch, `unload()`
  при смене модели.
- `processing/enhancer.py` — `download_model()` для GGUF.
- `utils/gpu.py` — детект через ctranslate2/nvidia-smi без раннего импорта torch.
- `web/server.py` — `/api/settings`, `/api/downloads*`, чинить `/api/gpu`,
  `/api/files` (ai/whisper_model/use_gpu).
- `web/index.html`, `web/script.js`, `web/style.css` — модал запуска, модал
  настроек, прогресс-модал, попапы.
- `build_zips.py` — один вариант с `models/ct2/tiny` и `llama/`, без GGUF/CUDA.
- `bootstrap.bat` — сообщения и проверки под новую схему.
- `MEMORY.md` — раздел о единой сборке.

## 11. Критерии приёмки

1. `build_zips.py` собирает один zip ~400–500 МБ; распаковка — запуск без сети
   даёт транскрибацию на `tiny` (CPU) сразу.
2. В модале «Обработать с ИИ» на свежей установке качает Gemma с прогрессом,
   после чего ИИ-улучшение работает.
3. Смена модели качает и удаляет предыдущую; приложение продолжает работать.
4. `use_gpu` на машине с NVIDIA: попап CUDA → скачивание → попап рестарта;
   после рестарта транскрибация на CUDA (`float16`).
5. Без NVIDIA чекбокс GPU недоступен с подписью.
6. Параметры модала сохраняются в `settings.json` и предзаполняют следующий запуск.
7. `/api/gpu` работает; регресс: перевод, «Улучшить с ИИ», skip, тайм-коды.
8. Лишнее не commit-ится (спека и код; GGUF/CUDA wheels не в гите).