# Транскрибация видео — Spec v0.2

## Цель

Пользователь перетаскивает видеофайл → автоматически извлекается аудиодорожка → транскрибация + улучшение текста. FFmpeg встроен в дистрибутив.

---

## 1. Список поддерживаемых видеоформатов

**`config.py`**
```python
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv", ".m4v", ".flv", ".ts"}
```

Output-папка, настройки Ollama и Whisper — без изменений.

---

## 2. `utils/files.py` — поиск файлов

- `find_supported_files(paths)` — замена `find_audio_files`, собирает и аудио и видео
- `is_video_file(path) -> bool` — проверка по суффиксу

---

## 3. `utils/extract_audio.py` — извлечение аудио

```python
def extract_audio(video_path: Path, ffmpeg_path: Path | str = "ffmpeg") -> Path
```

- Создаёт временный `.wav` (16kHz, mono, pcm_s16le) через `tempfile.NamedTemporaryFile`
- Вызывает `ffmpeg -i <video> -vn -acodec pcm_s16le -ar 16000 -ac 1 <output.wav> -y`
- Если ffmpeg не найден — `RuntimeError("FFmpeg not found")`
- Если в видео нет аудиодорожки — `ValueError("No audio stream")` (парсим stderr: `find_stream`)

**Служебная функция:**
```python
def get_ffmpeg_path() -> str
```
- Возвращает `str(BASE_DIR / "ffmpeg.exe")` если существует
- Иначе `"ffmpeg"` (системный PATH)

---

## 4. `web/server.py` — точка входа

- Заменить `find_audio_files` на `find_supported_files`
- После сохранения загруженного файла: если `is_video_file()` → `extract_audio()` → сохранить путь к `.wav` → удалить оригинальное видео
- Временный `.wav` удаляется в `CleanupStep` пайплайна (как и предобработанные файлы)

**Поток:**
```
POST /api/files
  ├─ video.mp4 → сохранить → extract_audio → video.wav
  ├─ audio.mp3 → сохранить как есть
  └─ video.avi → сохранить → extract_audio → video.wav
       └─ все wav → find_audio_files → pipeline
```

---

## 5. `web/index.html` — accept у input

```html
accept=".mp3,.wav,.m4a,.ogg,.flac,.aac,.wma,.mp4,.avi,.mkv,.mov,.webm,.wmv,.m4v,.flv,.ts"
```

---

## 6. `build_zips.py` — встраивание ffmpeg

На этапе сборки:

```python
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
```

1. Скачать `ffmpeg-release-essentials.zip`
2. Извлечь `ffmpeg.exe` (он внутри `ffmpeg-*-essentials_build/bin/ffmpeg.exe`)
3. Положить в корень билд-директории (`build_dir / "ffmpeg.exe"`)

Размер: ~55 MB  
Итоговый zip: CPU ~124+55 = ~179 MB, CUDA ~2486+55 = ~2541 MB

**Проверка:** `curl -L` → `7z x` или `python zipfile.extract` → переместить `ffmpeg.exe`

---

## 7. `bootstrap.bat`

- Убрать шаг 6 (проверка ffmpeg + предупреждение) — ffmpeg встроен
- Опционально: оставить `where ffmpeg` как fallback для пользовательского ffmpeg

---

## 8. Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| ffmpeg.exe отсутствует (удалён) | `RuntimeError` → SSE `ErrorEvent("FFmpeg не найден")` |
| В видео нет аудио | `ValueError` → SSE `ErrorEvent("В видео {name} нет аудиодорожки")` |
| Битый видеофайл | ffmpeg вернёт код != 0 → SSE `ErrorEvent("Ошибка извлечения аудио из {name}")` |
| Видео + аудио вместе | Извлекается только первая аудиодорожка |
| Очень длинное видео (>3 ч) | ffmpeg предупредит, но обработает (OOM маловероятен — потоковый wav не хранится в RAM) |

---

## 9. Ограничения

- Только **первая** аудиодорожка (если в видео несколько)
- Формат извлечения: WAV (PCM 16-bit, 16kHz, mono) — совместимость с Whisper
- Временный WAV удаляется после обработки (`CleanupStep`)

---

## 10. Версия

- v0.11.0

---

## 11. Файлы для изменения

| Файл | Действие |
|------|----------|
| `config.py` | + `VIDEO_EXTENSIONS`, + `FFMPEG_BUNDLED` |
| `utils/files.py` | + `find_supported_files`, + `is_video_file` |
| `utils/extract_audio.py` | **создать** |
| `web/server.py` | `find_audio_files` → `find_supported_files`, + вызов `extract_audio` |
| `web/index.html` | расширить `accept` |
| `build_zips.py` | + скачивание/встраивание ffmpeg.exe |
| `bootstrap.bat` | убрать проверку ffmpeg |
