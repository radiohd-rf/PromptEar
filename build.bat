@echo off
chcp 65001 >nul
title Сборка PromptEar

echo.
echo === Сборка PromptEar ===
echo.

:: Установка зависимостей для сборки
echo [1/3] Установка PyInstaller...
pip install pyinstaller --quiet
if errorlevel 1 (
    echo Ошибка при установке PyInstaller.
    pause
    exit /b 1
)

:: Сборка main.py (лёгкая — без torch/whisper/scipy, они ставятся отдельно)
echo [2/3] Сборка PromptEar.exe...
python -m PyInstaller ^
    --onedir ^
    --windowed ^
    --name PromptEar ^
    --noconfirm ^
    --clean ^
    --add-data "config.py;." ^
    --add-data "processing;processing" ^
    --add-data "utils;utils" ^
    --add-data "ui;ui" ^
    --hidden-import tkinterdnd2 ^
    --hidden-import docx ^
    --hidden-import requests ^
    --hidden-import pydub ^
    --exclude-module torch ^
    --exclude-module whisper ^
    --exclude-module numpy ^
    --exclude-module scipy ^
    main.py
if errorlevel 1 (
    echo Ошибка при сборке.
    pause
    exit /b 1
)

:: Проверка Inno Setup
echo [3/3] Проверка Inno Setup...
where iscc >nul 2>&1 || where "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" >nul 2>&1 || where "C:\Program Files\Inno Setup 6\ISCC.exe" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Inno Setup не найден.
    echo Враппер собран: dist\PromptEar.exe
    echo.
    echo Для сборки установщика установите Inno Setup 6:
    echo https://jrsoftware.org/isdl.php
    echo Запустите build_installer.bat после установки.
    pause
    exit /b 0
)

:: Сборка установщика
echo [4/3] Сборка установщика...
iscc installer.iss
if errorlevel 1 (
    echo Ошибка при сборке установщика.
    pause
    exit /b 1
)

echo.
echo === Готово! ===
echo.
echo Враппер:    dist\PromptEar.exe (~5 МБ)
echo Установщик: dist\PromptEar_Setup.exe
echo.
echo Установщик установит программу в Program Files,
echo скачает и настроит Python, Ollama и модель Qwen.
echo.
pause
