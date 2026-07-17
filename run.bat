@echo off
title PromptEar

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"

if not exist "%VENV_DIR%\Scripts\pythonw.exe" (
    echo.
    echo Virtual environment not found. Run bootstrap.bat first.
    echo.
    pause
    exit /b 1
)

start "" /B "%VENV_DIR%\Scripts\pythonw.exe" "%APP_DIR%run.pyw"
