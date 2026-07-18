# PromptEar — Developer Notes

## Stack

Python 3.12, Tkinter, Whisper, Ollama+Qwen, ffmpeg

## Архитектура

```
core/       pipeline, models, events, detector, topic, installers
ui/         root (оркестратор), theme, titlebar, handlers, widgets
processing/ transcriber, enhancer + re-export wrappers
utils/      files, gpu, logger, protocol
```

## Быстрый старт

```bash
pytest tests/unit/ -v         # 97 тестов
ruff check .                  # линтер
python main.py                # GUI
build.bat                     # сборка установщика + .exe
```

## Ключевые контракты

```
PipelineStep.process(result, config, emit, cancel) -> TranscriptionResult
emit = Callable[[PipelineEvent], None]
cancel = threading.Event
```

## Релизы

```bash
gh release create vX.Y.Z --title "..." dist/*.exe
```

Текущая: **v0.9.0**
