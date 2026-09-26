# Спека: адаптация под машины с AMD GPU (вариант сборки `amd`)

Дата: 2026-09-22. Статус: **предложение к согласованию** (решение по объёму и
варианту «отдельная сборка vs единый дистрибутив» — за юзером, см. §5).

## 1. Цель и контекст

Приложение сегодня ускоряет транскрибацию только на NVIDIA (`/api/gpu` детектит
исключительно nvidia-smi + `ctranslate2.get_cuda_device_count()`), а ИИ-улучшение
(gemma в llama-server) — всегда на CPU (в zip вшивается CPU-сборка llama.cpp).
На машинах с AMD GPU чекбокс GPU в модалах корректно блокируется («GPU не
обнаружен») и всё падает на CPU.

Задача — спроектировать поддержку AMD GPU. Как минимум для машин AMD, при
целесообразности — через **единый дистрибутив**, работающий на обеих архитектурах
(тогда отдельная сборка не нужна).

## 2. Текущее состояние (что мешает)

- `utils/gpu.py` — `detect_and_report()`: `has_nvidia_gpu()` (nvidia-smi → wmic
  «nvidia»), `ctranslate2_cuda_devices()`; ключи `has_nvidia_gpu`, `cuda_components`,
  `cuda_available`, `device`. AMD не детектится и не используется.
- `processing/transcriber.py:64-65` — `device="cuda" if settings.use_gpu and
  ctranslate2.get_cuda_device_count()>0 else "cpu"`; compute `float16`/`int8`.
- `processing/enhancer.py:758` — llama-server стартует с `--threads`, **без**
  GPU-оффлоада; в `models/llama/` лежит CPU-билд (config.py:52 `bin-win-cpu-x64.zip`).
- `build_zips.py` — единый вариант `PromptEar-<ver>.zip` (после рефакторинга #30).

## 3. Факты о движках (проверено 2026-09-22)

### 3.1 Транскрибация Whisper = faster-whisper → ctranslate2

- PyPI-колёса ctranslate2 (в т.ч. Windows x86-64) — **CUDA- и CPU-бэкенды**
  (документация Installation). GPU на Windows требует CUDA 12.x + cuDNN 8 → только NVIDIA.
- **ROCm/HIP-бэкенд добавлен в v4.7.0 (2026-02, PR #1989)**, v4.7.1 — «Fix Windows
  build». Сборка: `-DWITH_HIP=ON`, требует системные **ROCm-библиотеки**.
- ROCm-колёса публикуются **на GitHub-релизах**, не на PyPI; штатно это
  manylinux-артефакты. AMD ROCm официально поддерживается на Linux
  (+ WSL2), полноценного ROCm под Windows нет → **ctranslate2 ROCm на Windows
  как штатный путь неприменим**.
- Vulkan/OpenCL/DirectML бэкендов в ctranslate2 нет.

Вывод: **faster-whisper на AMD GPU на Windows не работает ни через какой видимый
флаг** — только CPU, либо смена движка транскрибации (см. §4, варианты B/C).

### 3.2 ИИ-улучшение = llama.cpp (gemma)

- llama.cpp официально публикует сборки под Windows, в т.ч. `bin-win-vulkan-x64.zip`.
  Vulkan работает на **AMD, NVIDIA и Intel** одинаково (через драйвер).
- `llama-server` принимает `-ngl N`/`--gpu-layers N` — с Vulkan-сборкой вся gemma
  уходит на GPU. Это включит GPU-ускорение ИИ **на всех** машинах, не только AMD.
- DirectML-бэкенд llama.cpp существует, но экспериментальный; Vulkan приоритетнее.

### 3.3 Альтернативный движок транскрибации

- **whisper.cpp (ggml-org/whisper.cpp)** — официальная Windows-сборка с Vulkan
  (`-DGGML_VULKAN=ON`) поддерживает AMD. Модели — GGML-формат (`ggml-base.bin`…),
  **не** CT2-формат → меняются каталог моделей, скачивание, API, стриминг сегментов.
- **ONNX Runtime DirectML** (whisper в ONNX): работает на Windows на любом GPU,
  но требует экспорт Whisper в ONNX (оптимум/onnx transformers) и новый движок.

## 4. Варианты (для решения)

| Вариант | ROI на AMD | Риск/труд | Влияние на NVIDIA-машины |
|---|---|---|---|
| **A. Только ИИ на GPU (llama Vulkan)**, транскрибация остаётся CPU на AMD | ИИ ускоряется, т.е. главный источник «думает» на GPU; транскрибация — средне | Низкий: заменить zip в сборке и добавить `-ngl` | + Ускоряет ИИ и на NVIDIA |
| **B. + Транскрибация через whisper.cpp Vulkan** | Полный GPU на AMD | Высокий: новый движок, GGML-модели, переписать TranscribeStep/стриминг | = Потеря faster-whisper-фич (align, hotwords, CPU-int8 удобства); риск регрессов тайм-кодов/перевода |
| **C. + ONNX DirectML транскрибация** | Полный GPU на AMD | Очень высокий: экспорт моделей, новый рантайм | Потенциально ускоряет и на NVIDIA/Intel iGPU, но большая переделка |
| **D. Ничего не делать** | GPU никогда | 0 | — |

Рекомендация по-этапно:
1. **Этап 1 = Вариант A** отдельной сборкой `amd` (низкий риск, закрывает «ИИ на
   CPU» на AMD; единый код, бэкенд определяет сборку). NV-сборка не меняется в
   поведении (или тоже переходит на Vulkan-llama — опция).
2. **Этап 2 (опционально)** — решить B/C отдельно: требует замера качества и
   регресс-теста тайм-кодов/перевода/стриминга; по объёму это отдельный проект.

«Отдельная сборка» оправдана только для Этапа 2 (разные движки транскрибации).
Для Этапа 1 возможен как `amd`-вариант, так и единый дистрибутив: решение in §5.

## 5. Открытые вопросы (нужны решения юзера)

1. Объём: только **Этап 1** или сразу планировать Этап 2 (transcription GPU)?
2. Дистрибутив для Этапа 1:
   - **5a. Отдельная сборка `amd`** — в `build_zips.py` второй вариант
     `PromptEar-<ver>-amd.zip` (вместо `llama/`-cpu кладётся `llama/`-vulkan);
     `run.bat`/`config` подхватывают `LLM_GPU_LAYERS`.
   - **5b. Единый дистрибутив** — всем машинам Vulkan-lama + `-ngl`, авто;
     проще поддержка, один zip, минус — для этапа 2 всё равно нужны отдельные сборки.
3. Параметр `-ngl`: фиксировать `--gpu-layers 99` (вся модель) или выставлять из
   настроек (например, поле «слои GPU» в настройках)? По умолчанию — вся модель.
4. Нужен ли индикатор «ИИ на GPU/CPU» в футере рядом со статусом транскрибации?

## 6. Детект GPU и API

Расширить `utils/gpu.py` и `/api/gpu` без поломки NV-семантики:

- `has_amd_gpu()` — wmic win32_videocontroller: имя содержит `amd`/`radeon`
  (+ задел под `intel`/`arc`).
- `detect_and_report()` возвращает (для обоих вариантов сборки):
  - `gpu_vendor`: `"nvidia" | "amd" | "intel" | null` (по первому найденному);
  - `has_nvidia_gpu` — как сейчас (для NV-сборки и вшитой логики);
  - `has_amd_gpu`: bool;
  - `transcribe_device`: `"cuda|rocm|cpu|vulkan"` — фактический бэкенд транскрибации;
  - `llm_device`: `"cpu"` (сейчас) / `"vulkan"` (этап 1) — фактический бэкенд ИИ;
  - существующие `cuda_components`, `cuda_available`, `device` — не удалять,
    задокументировать как «относятся к транскрибации».
- UI: чекбокс «Использовать GPU» (модалы запуска/настроек):
  - в `amd`-сборке подпись при наличии AMD: «Использовать GPU (AMD, Vulkan)»;
  - блокировка чекбокса только если `!has_nvidia_gpu && !has_amd_gpu` (для амд-сборки);
  - hint про транскрибацию менять: на AMD «ускорение ИИ», транскрибация — CPU.

## 7. Вариант сборки `amd` (для Этапа 1)

`build_zips.py`: вернуть вариантную логику (`VARIANTS = {"default": "cpu-llama",
"amd": "vulkan-llama"}`):

- Разница только в содержимом `llama/`: `bin-win-cpu-x64.zip` → `bin-win-vulkan-x64.zip`.
- `models/ct2/<base>` — та же (вшивается base, как в едином дистрибутиве).
- GGUF gemma по-прежнему НЕ вшивается (качается в рантайме по `config.GGUF_URL`).
- Вес zip меняется незначительно (~+10–30 МБ, шейдеры/драйвер Vulkan не нужны —
  они системные).

`config.py` (md-сборкой фиксируется в момент build, читается рантаймом):

```python
LLM_ENGINE = "llama"
LLAMA_VARIANT = "amd"  # "default" | "amd"
LLM_GPU_LAYERS = int(os.environ.get("PROMPTEAR_LLM_GPU_LAYERS", "99"))  # этап 1
```

`processing/enhancer.py` `_start_server()`: если `LLAMA_VARIANT == "amd"` и
`settings.use_gpu` → добавить в argv `--n-gpu-layers <LLM_GPU_LAYERS>` (проверено:
llama-server vulkan валит с понятной ошибкой при отсутствии GPU-драйвера — см.
стейт #20-llm про `_llama_last_error`). Без `use_gpu` → без `-ngl` (CPU).

## 8. Изменяемые файлы

- `utils/gpu.py` — `has_amd_gpu()`, расширенный report (§6).
- `web/server.py` — `/api/gpu` пробрасывает новые ключи (кэш не трогаем).
- `web/index.html`, `web/script.js`, `web/style.css` — формулировки к БОЛЬШЕ GPU,
  подписи AMD/Vulkan, hint-тексты («Распознавание ускоряется на NVIDIA; AMD — ИИ»).
- `processing/enhancer.py` — `-ngl` для amd-варианта; причина падения → `_llama_last_error`.
- `config.py` — `LLAMA_VARIANT`, `LLM_GPU_LAYERS`.
- `build_zips.py` — варианты `default`/`amd` (llama vulkan для `amd`).
- `bootstrap.bat` — текст под вариант (без изменений логики).
- `specs/unified-build.md` — сноска «на базе разд. amd-gpu-build».
- `MEMORY.md` — раздел по итогам решения.
- (Этап 2, если решён) — `processing/transcriber.py`, `core/whisper_models.py`
  (GGML), отдельные сборки.

## 9. Критерии приёмки (Этап 1)

1. На машине с AMD: установка `PromptEar-<ver>-amd.zip` → футер «ИИ-улучшение на
   GPU (Vulkan)», ИИ-обработка реально быстрее CPU (замер до/после).
2. На AMD без `use_gpu` — llama работает как раньше (CPU), никаких попапов/ошибок.
3. `/api/gpu` возвращает `gpu_vendor:"amd"`, `llm_device:"vulkan"`,
   `transcribe_device:"cpu"`; старые ключи целы (11-api.md не регрессит).
4. NV-сборка и поведение `use_gpu` на NVIDIA не меняются (регресс-тест 05/11).
5. Тайм-коды, перевод, стриминг, skip — без изменений (это используется в этапе 1).
6. `build_zips.py amd` собирает zip с vulkan-llama в `llama/`, без GGUF в нём.

## 10. Не-цель

- Не переносим транскрибацию на AMD на этом этапе (варианты B/C — отдельный
  проект с замером качества; faster-whisper — ключевая фича точных тайм-кодов).
- ROCm ctranslate2 под Windows — исключено (нет официальной поддержки ROCm на
  Windows и Windows-колёс; документировано в §3.1).