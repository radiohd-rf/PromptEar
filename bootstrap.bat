@echo off
chcp 65001 >nul
title PromptEar — установка зависимостей

echo.
echo === PromptEar — установка зависимостей ===
echo.
echo Установка Python, библиотек, Ollama и модели Qwen 2.5 3b.
echo Это может занять 15–20 минут в зависимости от скорости интернета.
echo.
echo Не закрывайте это окно до завершения.
echo.

set "APP_DIR=%~dp0"
set "PYTHON_DIR=%APP_DIR%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"

:: ---------------------------------------------------------------
:: 1. Embeddable Python
:: ---------------------------------------------------------------
if not exist "%PYTHON_EXE%" (
    echo [1/6] Скачивание Python 3.12.9 embeddable...
    curl -L -# -o "%TEMP%\python-embed.zip" "https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip"
    if %errorlevel% neq 0 (
        echo ОШИБКА: Не удалось скачать Python.
        pause
        exit /b 1
    )

    echo [2/6] Распаковка Python...
    if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%"
    mkdir "%PYTHON_DIR%"
    powershell -Command "Expand-Archive -Path '%TEMP%\python-embed.zip' -DestinationPath '%PYTHON_DIR%'"
    if %errorlevel% neq 0 (
        echo ОШИБКА: Не удалось распаковать Python.
        pause
        exit /b 1
    )

    :: Включаем site-packages в embeddable Python
    set "_PTH=%PYTHON_DIR%\python._pth"
    if exist "%_PTH%" (
        powershell -Command "(Get-Content '%_PTH%') -replace '#import site','import site' | Set-Content '%_PTH%'"
    )

    echo [3/6] Установка pip...
    curl -L -# -o "%TEMP%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
    "%PYTHON_EXE%" "%TEMP%\get-pip.py" --no-warn-script-location
    if %errorlevel% neq 0 (
        echo ОШИБКА: Не удалось установить pip.
        pause
        exit /b 1
    )
) else (
    echo [1/6] Python уже установлен
    echo [2/6] Пропущено
    echo [3/6] Пропущено
)

:: ---------------------------------------------------------------
:: 4. Установка Python-зависимостей
:: ---------------------------------------------------------------
echo [4/6] Установка библиотек...
:: Проверка NVIDIA GPU
echo Проверка видеокарты...
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>&1
if %errorlevel% equ 0 (
    echo NVIDIA GPU обнаружен — установка torch с CUDA...
    set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
) else (
    echo NVIDIA GPU не обнаружен — установка CPU-версии torch...
)

"%PYTHON_EXE%" -m pip install --quiet --no-warn-script-location ^
    torch torchaudio --index-url %TORCH_INDEX%
"%PYTHON_EXE%" -m pip install --quiet --no-warn-script-location ^
    openai-whisper>=20231117 ^
    python-docx>=1.1.0 ^
    requests>=2.28.0 ^
    tkinterdnd2
if %errorlevel% neq 0 (
    echo ОШИБКА: Не удалось установить библиотеки.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------
:: 5. Проверка / установка Ollama
:: ---------------------------------------------------------------
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo [5/6] Скачивание и установка Ollama...
    curl -L -# -o "%TEMP%\OllamaSetup.exe" "https://ollama.com/download/OllamaSetup.exe"
    if %errorlevel% neq 0 (
        echo ОШИБКА: Не удалось скачать Ollama.
        pause
        exit /b 1
    )
    start /wait "" "%TEMP%\OllamaSetup.exe" /S
    if %errorlevel% neq 0 (
        echo ОШИБКА: Не удалось установить Ollama.
        pause
        exit /b 1
    )
) else (
    echo [5/6] Ollama уже установлена
)

:: Ожидание запуска Ollama
echo Ожидание запуска Ollama...
:wait_ollama
ollama --version >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 3 /nobreak >nul
    goto wait_ollama
)

:: ---------------------------------------------------------------
:: 6. Скачивание модели Qwen
:: ---------------------------------------------------------------
echo [6/6] Скачивание модели qwen2.5:3b...
ollama pull qwen2.5:3b
if %errorlevel% neq 0 (
    echo ОШИБКА: Не удалось скачать модель.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------
:: Готово
:: ---------------------------------------------------------------
echo.
echo === Установка зависимостей завершена! ===
echo.
echo Python:       %PYTHON_DIR%
echo Ollama:       установлена
echo Модель Qwen:  qwen2.5:3b
echo.
timeout /t 5 /nobreak >nul
