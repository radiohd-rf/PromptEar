@echo off
chcp 65001 >nul
title PromptEar

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo Виртуальное окружение не найдено.
    echo.
    echo Запустите bootstrap.bat для установки зависимостей.
    echo.
    pause
    exit /b 1
)

"%VENV_DIR%\Scripts\python.exe" "%APP_DIR%main.py"
if %errorlevel% neq 0 (
    echo.
    echo Ошибка запуска. Проверьте логи: %%APPDATA%%\PromptEar\logs\app.log
    pause
)
