# Очистка временных файлов

## Цель
Гарантированно удалять все временные файлы, созданные при транскрибации (извлечение аудио из видео + препроцессинг). Ничего не должно висеть ни в `%TEMP%`, ни где-либо ещё.

## Решение

Все временные файлы создаются в `<корень программы>/temp/<task_id>/`.
Каждый файл трекается через `AudioFile.temp_path` и удаляется либо в `CleanupStep`,
либо в `finally` блоке `_process_files` (страховка от сбоев).

## Изменения по файлам

### 1. `config.py` — новая константа

```python
TEMP_DIR: Final[Path] = BASE_DIR / "temp"
```

### 2. `utils/extract_audio.py` — путь вместо `tempfile`

- `TEMP_DIR` импортируется из `config`
- Вместо `tempfile.NamedTemporaryFile(suffix=".wav", delete=False)`:

```python
TEMP_DIR.mkdir(parents=True, exist_ok=True)
wav_path = TEMP_DIR / f"{uuid.uuid4().hex}.wav"
```

### 3. `core/detector.py` — `AudioDetector.preprocess()` в `TEMP_DIR`

- Вместо `tempfile.NamedTemporaryFile(suffix=".wav", delete=False)` — UUID-файл в `TEMP_DIR`

### 4. `core/pipeline.py` — `CleanupStep`

- Без изменений. Удаляет `result.audio.temp_path`.
- `DetectPreprocessStep`: не терять старый `temp_path` при перезаписи.

```python
old_tmp = result.audio.temp_path
if preproc_path:
    if old_tmp and old_tmp != preproc_path and old_tmp.exists():
        old_tmp.unlink(missing_ok=True)
    result.audio.temp_path = preproc_path
```

### 5. `web/server.py` — инициализация `temp_path` + очистка

**5.1.** При создании задачи — создать подпапку `TEMP_DIR / task_id`.

**5.2.** При извлечении аудио из видео — сохранять пути в `temp_wavs: set[Path]`.

**5.3.** При создании `AudioFile`:

```python
audio_files = [
    AudioFile(
        path=Path(f),
        temp_path=Path(f) if Path(f) in temp_wavs else None
    )
    for f in files
]
```

**5.4.** В `finally` блоке `_process_files` — страховочная очистка:

```python
finally:
    shutil.rmtree(task_temp_dir, ignore_errors=True)
    emit_queue.put_nowait({"type": "__done__"})
```

### 6. Импорты

- `web/server.py`: добавить `import shutil`, `TEMP_DIR` из `config`
- `utils/extract_audio.py`: добавить `import uuid`, `TEMP_DIR` из `config`
- `core/detector.py`: добавить `import uuid`, `TEMP_DIR` из `config`

## Trace сценариев

| Сценарий | Создано | Когда удалено |
|---|---|---|
| Аудиофайл | `temp/<id>/<uuid>.wav` (preproc) | `CleanupStep` |
| Видео, preproc успешен | `temp/<id>/<a>.wav` (extract) → `temp/<id>/<b>.wav` (preproc) | extract в `DetectPreprocessStep`, preproc в `CleanupStep` |
| Видео, preproc упал | `temp/<id>/<a>.wav` (extract) | `CleanupStep` |
| Любой сбой | все файлы в `temp/<id>/` | `shutil.rmtree` в `finally` |

## Структура папок

```
temp/
├── a1b2c3d4e5f6/
│   ├── abcd1234.wav   (extracted from video)
│   └── efgh5678.wav   (preprocessed)
├── f6e5d4c3b2a1/
│   └── 1234abcd.wav   (extracted)
└── ...
```
