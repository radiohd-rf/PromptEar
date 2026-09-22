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

"%PYTHON%" -m pip install --upgrade pip --quiet --no-cache-dir

echo [4/6] Installing libraries...

if exist "%APP_DIR%wheels\*.whl" (
    echo   Installing from local wheels...
    "%PIP%" install --no-index --find-links "%APP_DIR%wheels" torch torchaudio flask pywebview faster-whisper Pillow python-docx requests --quiet --no-cache-dir || (
        echo   ERROR: pip install failed.
        echo   Run manually: "%PIP%" install --no-index --find-links "%APP_DIR%wheels" torch torchaudio flask pywebview faster-whisper Pillow python-docx requests
        pause
        exit /b 1
    )
) else (
    echo   No local wheels found, installing from PyPI...
    "%PIP%" install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet --no-cache-dir || (
        echo   Warning: torch install failed
    )
    "%PIP%" install flask pywebview faster-whisper Pillow python-docx requests --quiet --no-cache-dir || (
        echo   Warning: some packages failed to install
    )
)
"%PIP%" install transformers huggingface-hub --quiet --no-cache-dir
if %errorlevel% neq 0 (
    echo   Warning: transformers not installed (SAGE engine disabled).
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
:: 6. Check / install llama.cpp (llama-server)
:: ---------------------------------------------------------------
echo [6/6] Checking llama.cpp...

if exist "%APP_DIR%llama\llama-server.exe" (
    echo   llama.cpp already ready
) else (
    echo   Downloading llama.cpp (CPU build)...
    for /f "delims=" %%i in ('powershell -NoProfile -Command "try { (Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30' -TimeoutSec 10) | Where-Object { \$_.assets.name -match 'win-cpu-x64' } | Select-Object -First 1 -ExpandProperty tag_name } catch { }"') do set "LLAMA_RELEASE=%%i"
    if "%LLAMA_RELEASE%"=="" set "LLAMA_RELEASE=b11034"
    curl -L -# -o "%TEMP%\llama.zip" "https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_RELEASE%/llama-%LLAMA_RELEASE%-bin-win-cpu-x64.zip"
    if not exist "%TEMP%\llama.zip" (
        echo   ERROR: Failed to download llama.cpp.
        pause
        exit /b 1
    )
    mkdir "%APP_DIR%llama" >nul 2>&1
    powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\llama.zip' -DestinationPath '%APP_DIR%llama' -Force"
    for /r "%APP_DIR%llama" %%f in (llama-server.exe) do set "FOUND_SERVER=%%f"
    if defined FOUND_SERVER (
        move /y "%FOUND_SERVER%" "%APP_DIR%llama\llama-server.exe" >nul 2>&1
        echo   llama-server ready
    ) else (
        echo   ERROR: llama-server.exe not found in archive.
        pause
        exit /b 1
    )
)

:: Check model
echo   Checking whisper model...
if exist "%APP_DIR%models\ct2\tiny\model.bin" (
    echo   Whisper tiny found
) else (
    echo   Whisper tiny not embedded — will be downloaded on first run.
)

:: ---------------------------------------------------------------
:: Done
:: ---------------------------------------------------------------
echo.
echo === Setup complete! ===
echo.
echo Virtual env: %VENV_DIR%
echo LLM engine:  llama.cpp (llama-server)
echo Whisper:     tiny (embedded), others download on demand
echo.
echo Run: run.bat
echo.
echo Model AI and CUDA components download from the app if needed.
echo.
echo Press any key to exit...
pause >nul
