@echo off
title PromptEar

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"

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

"%VENV_DIR%\Scripts\python.exe" "%APP_DIR%main.py"
pause
