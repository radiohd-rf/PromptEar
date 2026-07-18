@echo off
title PromptEar - Setup

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python-3.12.9-amd64.exe"

:: ---------------------------------------------------------------
:: 1. Check / install Python 3.12
:: ---------------------------------------------------------------
echo [1/6] Checking Python...

where python >nul 2>&1
if %errorlevel% equ 0 (
    python --version 2>&1 | findstr "3.12" >nul
    if %errorlevel% equ 0 (
        echo   Python 3.12 found
        goto :python_ok
    )
)

echo   Python 3.12 not found. Downloading...
curl -L -# -o "%PYTHON_INSTALLER%" "%PYTHON_URL%"
if %errorlevel% neq 0 (
    echo   ERROR: Failed to download Python.
    echo   Download manually: https://www.python.org/downloads/release/python-3129/
    pause
    exit /b 1
)

echo   Installing Python...
start /wait "" "%PYTHON_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
if %errorlevel% neq 0 (
    echo   ERROR: Failed to install Python.
    pause
    exit /b 1
)
echo   Python installed

:python_ok

:: ---------------------------------------------------------------
:: 2. Create venv
:: ---------------------------------------------------------------
echo [2/6] Creating virtual environment...

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Virtual environment already exists
) else (
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo   ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo   Virtual environment created
)

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

:: ---------------------------------------------------------------
:: 3. Install Python dependencies
:: ---------------------------------------------------------------
echo [3/6] Updating pip...
"%PYTHON%" -m pip install --upgrade pip --quiet

echo [4/6] Installing libraries...

:: Detect NVIDIA GPU via Python (reliable, handles wmic encoding)
set "TORCH_INDEX="
set "GPU_SCRIPT=%TEMP%\gpu_check.py"
> "%GPU_SCRIPT%" echo import subprocess, shutil
>> "%GPU_SCRIPT%" echo s = shutil.which("nvidia-smi")
>> "%GPU_SCRIPT%" echo if s:
>> "%GPU_SCRIPT%" echo     r = subprocess.run([s, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, timeout=10)
>> "%GPU_SCRIPT%" echo     if r.returncode == 0 and r.stdout.strip():
>> "%GPU_SCRIPT%" echo         exit(0)
>> "%GPU_SCRIPT%" echo r = subprocess.run(["wmic", "path", "win32_videocontroller", "get", "name"], capture_output=True, text=True, timeout=5)
>> "%GPU_SCRIPT%" echo exit(0 if "nvidia" in r.stdout.lower() else 1)
"%PYTHON%" "%GPU_SCRIPT%" >nul 2>&1
if %errorlevel% equ 0 (
    echo   NVIDIA GPU detected - installing torch with CUDA...
    set "TORCH_INDEX=https://download.pytorch.org/whl/cu126"
) else (
    echo   No NVIDIA GPU detected - installing CPU torch...
)

if "%TORCH_INDEX%"=="" (
    "%PIP%" list --format=columns 2>nul | findstr /i "torch " >nul
    if %errorlevel% equ 0 (
        echo   torch already installed, skipping
    ) else (
        "%PIP%" install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cpu
        if %errorlevel% neq 0 (
            echo   Warning: torch installation failed, continuing...
        )
    )
) else (
    echo   Removing old venv for clean CUDA install...
    rmdir /s /q "%VENV_DIR%"
    python -m venv "%VENV_DIR%"
    set "PIP=%VENV_DIR%\Scripts\pip.exe"
    set "PYTHON=%VENV_DIR%\Scripts\python.exe"
    "%PYTHON%" -m pip install --upgrade pip --quiet
    echo   Installing torch with CUDA...
    "%PIP%" install --upgrade torch==2.11.0+cu126 torchaudio==2.11.0+cu126 --index-url %TORCH_INDEX% --trusted-host download.pytorch.org
    if %errorlevel% neq 0 (
        echo   Retrying without version pin...
        "%PIP%" install --upgrade torch torchaudio --index-url %TORCH_INDEX% --trusted-host download.pytorch.org
        if %errorlevel% neq 0 (
            echo   Falling back to CPU torch...
            "%PIP%" install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cpu
            pause
        )
    )
)

"%PIP%" list --format=columns 2>nul | findstr /i "whisper " >nul
if %errorlevel% equ 0 (
    echo   whisper already installed, skipping
) else (
    "%PIP%" install --quiet openai-whisper python-docx requests tkinterdnd2
    if %errorlevel% neq 0 (
        echo   ERROR: Failed to install libraries.
        pause
        exit /b 1
    )
)
echo   Libraries installed

:: ---------------------------------------------------------------
:: 4. Check FFmpeg
:: ---------------------------------------------------------------
echo [5/6] Checking FFmpeg...

where ffmpeg >nul 2>&1
if %errorlevel% equ 0 (
    echo   FFmpeg found
) else (
    echo   FFmpeg not found.
    echo   Install: winget install ffmpeg
    echo   Or download: https://ffmpeg.org/download.html
)

:: ---------------------------------------------------------------
:: 5. Check / install Ollama
:: ---------------------------------------------------------------
echo [6/6] Checking Ollama...

where ollama >nul 2>&1
if %errorlevel% equ 0 (
    echo   Ollama already installed
) else (
    echo   Downloading Ollama...
    curl -L -# -o "%TEMP%\OllamaSetup.exe" "https://ollama.com/download/OllamaSetup.exe"
    if %errorlevel% neq 0 (
        echo   ERROR: Failed to download Ollama.
        pause
        exit /b 1
    )
    start /wait "" "%TEMP%\OllamaSetup.exe" /S
    if %errorlevel% neq 0 (
        echo   ERROR: Failed to install Ollama.
        pause
        exit /b 1
    )
    echo   Ollama installed
)

:: Wait for Ollama to start
echo   Waiting for Ollama...
:wait_ollama
ollama --version >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 3 /nobreak >nul
    goto wait_ollama
)

:: Pull Qwen model (skip if already present)
echo   Checking qwen2.5:3b model...
ollama list 2>nul | findstr "qwen2.5:3b" >nul
if %errorlevel% equ 0 (
    echo   qwen2.5:3b already pulled, skipping
) else (
    echo   Pulling qwen2.5:3b model...
    ollama pull qwen2.5:3b
)
if %errorlevel% neq 0 (
    echo   Warning: Failed to pull model.
    echo   Run later: ollama pull qwen2.5:3b
)

:: ---------------------------------------------------------------
:: Done
:: ---------------------------------------------------------------
echo.
echo === Setup complete! ===
echo.
echo Virtual env: %VENV_DIR%
echo Ollama:      installed
echo Qwen model:  qwen2.5:3b
echo.
echo Run: run.bat
echo.
echo.
echo Нажмите любую клавишу для выхода...
pause >nul
