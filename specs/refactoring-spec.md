# Спецификация: Рефакторинг + Оптимизация + Архитектурный редизайн PromptEar

**Версия:** 1.0
**Статус:** Черновик

---

## 1. Цель

Устранить архитектурные долги, повысить тестируемость, улучшить поддерживаемость,
без изменения внешнего поведения и пользовательского опыта.

---

## 2. Текущая архитектура (as-is)

```
main.py ──► PromptEarApp (app.py) ◄── processing/{transcriber,enhancer,detector,topic}
                 │                             utils/{files,gpu,logger,protocol}
                 │                             ui/widgets.py
                 │
           ┌─────┴──────────┐
           │ ВСЁ В ОДНОМ:   │
           │ UI-билдинг     │
           │ Theme-менеджмент│
           │ Pipeline       │
           │ CUDA install   │
           │ Queue handler  │
           │ CLI args       │
           └────────────────┘
```

### 2.1 Проблемы

| # | Проблема | Где | Влияние |
|---|----------|-----|---------|
| P1 | **God Object** | `app.py` ~1220 строк | Сложно читать, тестировать, менять |
| P2 | **Глобальные мутируемые переменные** | `global WHITE, FG, BORDER...` | Состояние гонки при переключении темы |
| P3 | **COLORS — изменяемый словарь** | `config.py` + `_toggle_theme` | `COLORS.update(DARK_COLORS)` мутирует общий объект |
| P4 | **Нет контрактов** | Сырые `dict`/`tuple` между модулями | Легко сломать, нет подсказок типа |
| P5 | **Смесь ответственности** | `_install_cuda()` (120 строк) внутри `app.py` | Нельзя переиспользовать, нельзя тестировать |
| P6 | **Theme — ручная рекурсия** | `_apply_theme()` рекурсивно по всем виджетам | Медленно, хрупко |
| P7 | **Whisper stdout hijack** | `transcriber.py` — `sys.stdout = ProgressCapture` | Грязный хак, не работает в threading |
| P8 | **Queue handler — монстр** | `_check_queue` (50 строк) свитч без типов | Трудно расширять |
| P9 | **topic.py — статический словарь** | Ключевые слова жёстко закодированы | Нельзя кастомизировать |

---

## 3. Целевая архитектура (to-be)

```
src/
├── main.py                         # Точка входа (не меняется)
├── app.py                          # ~100 строк: создание, run()
├── config.py                       # Не меняется (или const -> @dataclass)
│
├── ui/
│   ├── __init__.py
│   ├── root.py                     # PromptEarApp (оркестратор, ~200 строк)
│   ├── titlebar.py                 # Кастомный заголовок + drag + taskbar icon
│   ├── theme.py                    # ThemeManager: COLORS, DARK_COLORS, apply()
│   ├── widgets.py                  # PlaceholderEntry, PlaceholderListbox (уже есть)
│   └── handlers.py                 # _check_queue -> registry обработчиков
│
├── core/
│   ├── __init__.py
│   ├── pipeline.py                 # AudioPipeline (detect → preprocess → transcribe → enhance → save)
│   ├── transcriber.py              # WhisperTranscriber (чистый класс)
│   ├── enhancer.py                 # QwenEnhancer (с новым API)
│   ├── detector.py                 # AudioDetector (класс, а не функции)
│   ├── topic.py                    # TopicDetector (с возможностью кастомизации словаря)
│   ├── models.py                   # Entity: AudioFile, TranscriptionResult, PipelineStep, ...
│   └── events.py                   # Типизированные события вместо QueueMsg
│
└── utils/
    ├── files.py                    # find_audio_files, save_txt, save_docx
    ├── gpu.py                      # GPUDetector (класс)
    ├── logger.py                   # (без изменений)
    └── protocol.py                 # QueueMsg (возможно, замена на events.py)
```

### 3.1 Контракты (core/models.py)

```python
# Чёткие типы вместо сырых dict/tuple

@dataclass
class AudioFile:
    path: Path
    original_path: Path | None = None  # после предобработки
    is_quiet: bool = False

@dataclass
class TranscriptionResult:
    text: str
    audio: AudioFile
    duration_sec: float = 0
    whisper_confidence: float | None = None

@dataclass
class PipelineConfig:
    output_format: str = "docx"
    multi_pass: bool = False
    initial_prompt: str | None = None

# События для очереди GUI
@dataclass
class LogEvent:
    message: str

@dataclass
class ProgressEvent:
    current: int
    total: int
    filename: str
    eta: str

@dataclass
class TranscribingEvent:
    message: str

@dataclass
class WhisperProgressEvent:
    percent: int
    current: int
    total: int
    elapsed: str
    remaining: str
    speed: str

@dataclass
class OllamaReadyEvent:
    ollama_ok: bool
    model_ok: bool

@dataclass
class PipelineDoneEvent:
    message: str

@dataclass
class PipelineErrorEvent:
    message: str

@dataclass
class PipelineCancelledEvent:
    message: str
```

### 3.2 Pipeline (core/pipeline.py)

```python
class PipelineStep(ABC):
    @abstractmethod
    def process(self, result: TranscriptionResult, config: PipelineConfig) -> TranscriptionResult: ...
    @abstractmethod
    def name(self) -> str: ...

class AudioPipeline:
    steps: list[PipelineStep]  # [DetectStep, PreprocessStep, TranscribeStep, EnhanceStep, SaveStep]

    def run(
        self,
        files: list[AudioFile],
        config: PipelineConfig,
        event_callback: Callable[[object], None],
    ) -> None: ...
```

### 3.3 Theme (ui/theme.py)

```python
class ThemeManager:
    _current: str = "light"

    @property
    def colors(self) -> dict[str, str]: ...   # returns LIGHT_COLORS or DARK_COLORS
    @property
    def is_dark(self) -> bool: ...

    def toggle(self) -> None: ...             # no global mutation
    def apply_to(self, widget: tk.Widget) -> None: ...
    def apply_to_all(self, root: tk.Widget) -> None: ...  # один вызов при загрузке
```

### 3.4 Event Handler (ui/handlers.py)

Вместо `_check_queue` — реестр:

```python
class EventDispatcher:
    _handlers: dict[type, list[Callable]] = {}

    def register(self, event_type: type, handler: Callable): ...
    def dispatch(self, event: object): ...

# Usage:
dispatcher = EventDispatcher()
dispatcher.register(LogEvent, lambda e: self._log(e.message))
dispatcher.register(PipelineDoneEvent, lambda e: show_info(e.message))
```

---

## 4. Оптимизация

### 4.1 GPU Detection

- **Сейчас:** `detect_and_report()` вызывается в `__init__` — 3 вызова `subprocess.run`
- **Оптимизация:** Кешировать результат в `functools.cache` или в `@property`, одноразовый вызов

### 4.2 Whisper Model Load

- **Сейчас:** `load_model()` внутри `transcribe()`, нет прогресс-бара загрузки
- **Оптимизация:** Загружать модель асинхронно при старте (в фоне), показывать прогресс

### 4.3 Theme Toggle

- **Сейчас:** Рекурсивный обход (~0.5с на переключение)
- **Оптимизация:** `winfo_children()` — уже есть. Добавить кеш виджетов + пакетное обновление `configure`

### 4.4 FFmpeg Preprocessing

- **Сейчас:** Всегда single-pass через `subprocess.run`
- **Оптимизация:** Использовать `asyncio` или `threading` для потоковой обработки (опционально)

### 4.5 Ollama Keep-Alive

- **Сейчас:** Каждый запрос — новый HTTP-connection
- **Оптимизация:** `requests.Session()`, keep-alive, повторное соединение

---

## 5. План работ (фазы)

### Фаза 1: Разделение app.py (~2-3 дня)

**Цель:** Убрать God Object, вынести слои

| Шаг | Файл/Модуль | Что сделать |
|-----|-------------|-------------|
| 1.1 | `ui/titlebar.py` | Вынести `_setup_titlebar`, `_add_to_taskbar`, `_set_window_icon`, drag |
| 1.2 | `ui/theme.py` | Вынести тему: класс `ThemeManager` |
| 1.3 | `ui/handlers.py` | Вынести `_check_queue` -> EventDispatcher |
| 1.4 | `core/models.py` | Типизированные модели |
| 1.5 | `core/pipeline.py` | Вынести `_on_run` + worker |

### Фаза 2: Оптимизация (~1-2 дня)

**Цель:** Ускорить узкие места

| Шаг | Модуль | Что сделать |
|-----|--------|-------------|
| 2.1 | `utils/gpu.py` | Кеш GPU-детекции |
| 2.2 | `transcriber.py` | Асинхронная загрузка модели |
| 2.3 | `ui/theme.py` | Оптимизация apply_theme |
| 2.4 | `enhancer.py` | `requests.Session()` keep-alive |

### Фаза 3: Архитектурный редизайн (~3-4 дня)

**Цель:** Чистые контракты, pipeline-паттерн, событийная модель

| Шаг | Модуль | Что сделать |
|-----|--------|-------------|
| 3.1 | `core/events.py` | Заменить QueueMsg на типизированные события |
| 3.2 | `core/models.py` | Entity для AudioFile, TranscriptionResult |
| 3.3 | `core/pipeline.py` | PipelineStep chain + error handling |
| 3.4 | `core/detector.py` | AudioDetector класс (вместо функций) |
| 3.5 | `core/topic.py` | TopicDetector класс с кастомизируемым словарём |

---

## 6. Критерии готовности

1. `app.py` сокращён с 1220 до ≤150 строк
2. `global WHITE, FG, BORDER, PH, BTN_ACTIVE_BG` удалены
3. `COLORS.update()` заменён на `ThemeManager`
4. `_check_queue` заменён на `EventDispatcher` с типизированными событиями
5. Whisper stdout hijack заменён на `tqdm`/callback
6. Pipeline вынесен в `core/` с чёткими `PipelineStep` контрактами
7. Все существующие тесты зелёные
8. Внешнего поведения не изменилось
