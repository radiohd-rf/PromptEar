# PromptEar — Быстрый старт

## Что это

Настольное приложение для транскрипции аудио через Whisper с автоматическим улучшением текста через локальную LLM (Qwen 2.5 3b через Ollama).

## Зависимости

```
pip install openai-whisper python-docx requests
```

Опционально (drag-and-drop): `pip install tkinterdnd2`

## Как запустить

```bash
python main.py
```

## Структура проекта

```
PromptEar/
├── main.py               # Точка входа
├── app.py                # Окно приложения (PromptEarApp)
├── config.py             # Конфигурация (цвета, URL, модель, таймауты)
├── ui/
│   └── widgets.py        # PlaceholderEntry, PlaceholderListbox
├── processing/
│   ├── transcriber.py    # Whisper
│   ├── enhancer.py       # Ollama + Qwen (1-pass + 3-pass)
│   ├── detector.py       # Тихий звук + предобработка
│   └── topic.py          # Определение темы
├── utils/
│   ├── files.py          # Поиск аудио, сохранение txt/docx
│   ├── gpu.py            # GPU-детекция
│   └── protocol.py       # QueueMsg Enum
├── specs/                # Спецификации
└── requirements.txt
```

## Архитектура

```
main.py → PromptEarApp (app.py)
  ├─ _build_ui()         → GUI: поля, checkbox, спиннер, лог
  ├─ _on_run()           → worker в фоновом потоке
  │   ├─ detect_quiet_audio  → анализ громкости
  │   ├─ preprocess_audio    → компрессия (тихие записи)
  │   ├─ transcriber         → Whisper
  │   ├─ enhancer.enhance    → Qwen (1-pass) / enhance_multi_pass (3-pass)
  │   └─ save_txt/save_docx  → сохранение
  └─ _check_queue()      → QueueMsg → GUI (лог, спиннер, статус)
```

## Основные изменения в v2

| Было | Стало |
|------|-------|
| `transkrib.py` (монолит) | Модульная структура (ui/ processing/ utils/) |
| `ttk.Progressbar` | Unicode-спиннер + статус |
| Константы размазаны | Один `config.py` |
| `("log", msg)` magic strings | `QueueMsg.LOG` Enum |
| `transkrib.py` в корне | `main.py` в корне |
| Один проход Qwen | Опционально 3 прохода (очистка → стиль → структура) |

## Сборка exe

```bash
python -m PyInstaller --onefile --windowed --name PromptEar main.py
```

## Команды для разработки

```bash
python main.py                             # Запуск
python -m py_compile main.py               # Проверка синтаксиса
Remove-Item -Recurse -Force build, *.spec  # Очистка
```
