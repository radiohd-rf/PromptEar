@echo off
title PromptEar

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"

rem Порт llama-server: 8080 занят системным Apache (httpd), используем 8081.
rem Сам сервер поднимается один раз вручную/при установке и живёт отдельно.
set "PROMPTEAR_LLM_PORT=8081"

rem Обычный запуск — БЕЗ терминала: перезапускаем себя скрытно через wscript.
rem Консоль остаётся видимой только в ветке bootstrap (установка зависимостей).
if "%PROMPTEAR_HIDDEN%"=="1" goto :start
if not exist "%VENV_DIR%\Scripts\python.exe" goto :start
set "PROMPTEAR_HIDDEN=1"
if exist "%APP_DIR%hidden.vbs" (
    wscript //nologo "%APP_DIR%hidden.vbs" "%~f0"
    exit /b
)

:start
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [PromptEar] Virtual environment not found.
    echo [PromptEar] Running setup first...
    echo.
    call "%APP_DIR%bootstrap.bat"
    if %errorlevel% neq 0 (
        echo.
        echo [PromptEar] Setup failed. Please run bootstrap.bat manually.
        pause
        exit /b 1
    )
)

if exist "%VENV_DIR%\Scripts\pythonw.exe" (
    "%VENV_DIR%\Scripts\pythonw.exe" "%APP_DIR%main.py"
) else (
    "%VENV_DIR%\Scripts\python.exe" "%APP_DIR%main.py"
)
