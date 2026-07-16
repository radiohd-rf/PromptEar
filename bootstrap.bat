@echo off
chcp 65001 >nul
title PromptEar — установка зависимостей

echo.
echo === PromptEar — установка зависимостей ===
echo.
echo Будет выполнено:
echo   1. Проверка/установка Python 3.12
echo   2. Создание виртуального окружения (venv)
echo   3. Установка Python-библиотек (Whisper, Torch, docx, ...)
echo   4. Проверка FFmpeg
echo   5. Проверка/установка Ollama
echo   6. Скачивание модели Qwen 2.5 3b
echo.
echo Это может занять 15-30 минут в зависимости от скорости интернета.
echo.
echo Не закрывайте это окно до завершения.
echo.

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python-3.12.9-amd64.exe"

:: ---------------------------------------------------------------
:: 1. Проверка / установка Python
:: ---------------------------------------------------------------
echo [1/6] Проверка Python...

where python >nul 2>&1
if %errorlevel% equ 0 (
    python --version 2>&1 | findstr "3.12" >nul
    if %errorlevel% equ 0 (
        echo   Python 3.12 найден
        goto :python_ok
    )
)

echo   Python 3.12 не найден. Скачивание...
curl -L -# -o "%PYTHON_INSTALLER%" "%PYTHON_URL%"
if %errorlevel% neq 0 (
    echo   ОШИБКА: Не удалось скачать Python.
    echo   Скачайте вручную: https://www.python.org/downloads/release/python-3129/
    pause
    exit /b 1
)

echo   Установка Python...
start /wait "" "%PYTHON_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
if %errorlevel% neq 0 (
    echo   ОШИБКА: Не удалось установить Python.
    pause
    exit /b 1
)
echo   Python установлен

:python_ok

:: ---------------------------------------------------------------
:: 2. Создание venv
:: ---------------------------------------------------------------
echo [2/6] Создание виртуального окружения...

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Виртуальное окружение уже существует
) else (
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo   ОШИБКА: Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
    echo   Виртуальное окружение создано
)

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

:: ---------------------------------------------------------------
:: 3. Установка Python-зависимостей
:: ---------------------------------------------------------------
echo [3/6] Обновление pip...
"%PYTHON%" -m pip install --upgrade pip --quiet

echo [4/6] Установка библиотек...

:: Определяем torch (CPU или CUDA)
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>&1
if %errorlevel% equ 0 (
    echo   NVIDIA GPU обнаружен — устанавливаю torch с CUDA...
    set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
) else (
    echo   NVIDIA GPU не обнаружен — устанавливаю CPU-версию torch...
)

"%PIP%" install --quiet torch torchaudio --index-url %TORCH_INDEX%
if %errorlevel% neq 0 (
    echo   Предупреждение: torch не установился, продолжение...
)

"%PIP%" install --quiet openai-whisper python-docx requests tkinterdnd2
if %errorlevel% neq 0 (
    echo   ОШИБКА: Не удалось установить библиотеки.
    pause
    exit /b 1
)
echo   Библиотеки установлены

:: ---------------------------------------------------------------
:: 4. Проверка FFmpeg
:: ---------------------------------------------------------------
echo [5/6] Проверка FFmpeg...

where ffmpeg >nul 2>&1
if %errorlevel% equ 0 (
    echo   FFmpeg найден
) else (
    echo   FFmpeg не найден.
    echo   Установите: winget install ffmpeg
    echo   Или скачайте: https://ffmpeg.org/download.html
)

:: ---------------------------------------------------------------
:: 5. Проверка / установка Ollama
:: ---------------------------------------------------------------
echo [6/6] Проверка Ollama...

where ollama >nul 2>&1
if %errorlevel% equ 0 (
    echo   Ollama уже установлена
) else (
    echo   Скачивание Ollama...
    curl -L -# -o "%TEMP%\OllamaSetup.exe" "https://ollama.com/download/OllamaSetup.exe"
    if %errorlevel% neq 0 (
        echo   ОШИБКА: Не удалось скачать Ollama.
        pause
        exit /b 1
    )
    start /wait "" "%TEMP%\OllamaSetup.exe" /S
    if %errorlevel% neq 0 (
        echo   ОШИБКА: Не удалось установить Ollama.
        pause
        exit /b 1
    )
    echo   Ollama установлена
)

:: Ожидание запуска Ollama
echo   Ожидание запуска Ollama...
:wait_ollama
ollama --version >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 3 /nobreak >nul
    goto wait_ollama
)

:: Скачивание модели
echo   Скачивание модели qwen2.5:3b...
ollama pull qwen2.5:3b
if %errorlevel% neq 0 (
    echo   Предупреждение: Не удалось скачать модель.
    echo   Запустите позже: ollama pull qwen2.5:3b
)

:: ---------------------------------------------------------------
:: Готово
:: ---------------------------------------------------------------
echo.
echo === Установка завершена! ===
echo.
echo Виртуальное окружение: %VENV_DIR%
echo FFmpeg:              установлен
echo Ollama:              установлена
echo Модель Qwen:         qwen2.5:3b
echo.
echo Запустите приложение: run.bat
echo.
timeout /t 5 /nobreak >nul
