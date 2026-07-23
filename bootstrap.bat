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

if exist "%APP_DIR%wheels\*.whl" (
    echo   Installing from local wheels (offline)...
    "%PIP%" install --no-index --find-links "%APP_DIR%wheels" --quiet
) else (
    echo   No local wheels found, installing from PyPI...
    "%PIP%" install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet
    "%PIP%" install flask pywebview faster-whisper Pillow python-docx requests --quiet
)
if %errorlevel% neq 0 (
    echo   Warning: pip install reported an error.
)

echo   Libraries installed

:: ---------------------------------------------------------------
:: 5. Check / install WebView2 Runtime
:: ---------------------------------------------------------------
echo [5/6] Checking WebView2 Runtime...

reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" >nul 2>&1
if %errorlevel% equ 0 (
    echo   WebView2 Runtime already installed
) else (
    echo   Downloading WebView2 Runtime...
    curl -sS -L -o "%TEMP%\WebView2Setup.exe" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    if exist "%TEMP%\WebView2Setup.exe" (
        start /wait "" "%TEMP%\WebView2Setup.exe" /silent /install
        echo   WebView2 Runtime installed
    ) else (
        echo   Warning: Failed to download WebView2 Runtime
    )
)

:: ---------------------------------------------------------------
:: 6. Check / install Ollama
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

:: Pull Qwen model
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
echo Press any key to exit...
pause >nul
