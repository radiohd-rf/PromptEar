# Video Support — Spec v0.1

## Цель

Добавить поддержку видеоформатов: drag-and-drop видеофайлов → автоматическое извлечение аудиодорожки → транскрибация. FFmpeg встроен в дистрибутив.

## Изменения

### 1. `config.py`

```python
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv", ".m4v", ".flv", ".ts"}
FFMPEG_BUNDLED = True  # ffmpeg.exe лежит в корне дистрибутива
```

### 2. `utils/files.py`

- `find_supported_files(paths)` — замена `find_audio_files`, ищет и аудио и видео
- `is_video_file(path) -> bool`

### 3. Создать `utils/extract_audio.py`

```python
def extract_audio(video_path: Path, ffmpeg_path: Path | str = "ffmpeg") -> Path
```

- Извлекает аудио в temp `.wav` (16kHz, mono, pcm_s16le)
- Использует bundled ffmpeg или системный
- Возвращает путь к wav

### 4. `web/server.py`

- В обработчике загрузки файлов: если файл видео — `extract_audio()`, передать wav в пайплайн
- `find_audio_files` → `find_supported_files`

### 5. `web/index.html`

- `accept=".mp3,.wav,.m4a,.ogg,.flac,.aac,.wma,.mp4,.avi,.mkv,.mov,.webm,.wmv,.m4v,.flv,.ts"`

### 6. `build_zips.py`

- На этапе сборки: `curl -L` с gyan.dev → ffmpeg.exe (~55 MB)
- Добавить `ffmpeg.exe` в корень zip

### 7. `bootstrap.bat`

- Убрать предупреждение о ffmpeg (теперь встроен)
- Или оставить как fallback

### 8. Версия

- v0.11.0
