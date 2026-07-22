# Чек-лист: REST API

## Техники: EP, BVA, негатив

### POST /api/files

- [ ] `multipart/form-data`: 1 файл + output_format=docx + initial_prompt — успешно
- [ ] `multipart/form-data`: 5 файлов + output_format=txt + пустой prompt — успешно
- [ ] `multipart/form-data`: 0 файлов — 400 / ошибка валидации (минимум 1 файл)
- [ ] `multipart/form-data`: output_format=invalid — 400 / default формат
- [ ] `multipart/form-data`: video .mp4 — аудио извлекается, видео сохраняется
- [ ] Response: `{task_id, file_count}`

### GET /api/status/{task_id}

- [ ] Валидный task_id — SSE стрим с событиями
- [ ] Несуществующий task_id — 404
- [ ] Connect + первые события в течение 1 сек
- [ ] После done — стрим закрывается (__done__)

### POST /api/cancel/{task_id}

- [ ] Валидный task_id — cancel event срабатывает
- [ ] Несуществующий task_id — 404
- [ ] Двойной cancel — второй раз 200? (ищем идемпотентность)

### GET /api/download/{task_id}/{filename}

- **EP:** существующий файл, несуществующий файл, пустой файл
- [ ] Существующий файл — скачивание, верный Content-Type + Content-Disposition
- [ ] Несуществующий файл — 404
- [ ] `filename` с пробелами/кириллицей — корректный URL encode / декод
- **BVA:** `filename` пустой, `filename` = `../../etc/passwd` (path traversal — защита?)

### GET /api/gpu

- [ ] Возвращает: `has_nvidia_gpu, torch_cuda_installed, cuda_available, device, need_install`
- [ ] JSON — валидный

### GET /api/ollama

- [ ] Возвращает: `ollama_ok, model_ok`
- [ ] JSON — валидный

### GET /api/open-output

- [ ] Открывает Explorer с output/ папкой
- [ ] Если папки нет — создаёт? (проверить)

### Негативные сценарии

- [ ] Невалидный JSON в SSE — не ломает EventSource
- [ ] Concurrent requests — 2 параллельных POST /api/files — поведение? (busy)
- [ ] GET /api/status с task_id законченной задачи — 404? или последний статус?
