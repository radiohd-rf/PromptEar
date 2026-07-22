# Чек-лист: Установка и сборка

## 🛠️ Чем проверять

- Командная строка — запускай bootstrap.bat, run.bat, uninstall.bat, смотри вывод
- Process Explorer — проверь Job Object, python.exe, ffmpeg.exe
- Проводник — загляни в venv/, wheels/, output/ — всё ли на месте
- 7-Zip / WinRAR — открой zip после build_zips, проверь структуру
- Python — `python build_zips.py cpu`, `python build_zips.py cu126`

## Техники: State Transition, EP, негатив

### Bootstrap (bootstrap.bat)

- [ ] **Clean install:** чистая система → bootstrap скачивает Python 3.12, создаёт venv, устанавливает пакеты
- [ ] **Python 3.12 уже установлен:** пропускает скачивание
- [ ] **Венв уже существует:** пропускает создание
- [ ] **Wheels в папке:** устанавливает torch/torchaudio из ./wheels/
- [ ] **WebView2:** проверяет реестр, скачивает, если нет
- [ ] **Ollama:** скачивает и устанавливает, тянет qwen2.5:3b
- [ ] Повторный запуск bootstrap — идемпотентность
- [ ] **Bootstrap с флагом --silent — без вопросов

### Launch (run.bat / Запустить PromptEar.exe)

- [ ] Launcher (C#) запускается без консольного окна
- [ ] Launcher проверяет venv, запускает bootstrap при необходимости
- [ ] Job Object: при закрытии лаунчера python.exe убивается
- [ ] При падении Python — crash.log с traceback и диалог ошибки

### Uninstall (uninstall.bat)

- [ ] Интерактивный режим — выбор компонентов
- [ ] `--silent` — удаление без вопросов
- [ ] Удаляет: папку программы, venv, кеш Whisper, Ollama, Qwen, настройки и логи
- [ ] Каждый компонент удаляется отдельно (выбор из списка)

### CUDA Installer

- [ ] При запуске на GPU-машине: предлагает установить CUDA-версию torch
- [ ] Удаляет CPU torch → устанавливает cu126
- [ ] Верификация: torch.cuda.is_available() = True
- [ ] Fallback: если CUDA не установилась → возврат к CPU-версии

### Build ZIP (build_zips.py)

- **EP:** `cpu`, `cu126`
- [ ] `python build_zips.py cpu` — собирает CPU-дистрибутив
- [ ] `python build_zips.py cu126` — собирает CUDA-дистрибутив
- [ ] В архиве: исходники, wheels/, ffmpeg.exe, лаунчер, скрипты
- [ ] Исключено: __pycache__, .git, .github, .pytest_cache, .ruff_cache
- [ ] ffmpeg.exe скачивается с gyan.dev
- [ ] Версия в названии: PromptEar-v{version}-{variant}.zip

### Негативные сценарии

- [ ] Нет интернета при bootstrap — ошибка с предложением повторить
- [ ] Нет прав на C:\ при установке (если не администратор) — корректная ошибка
- [ ] Путь с пробелами/кириллицей — установка работает
- [ ] Ollama уже занята (порт 11434) — проверка
