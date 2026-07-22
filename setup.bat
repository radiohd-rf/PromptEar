@echo off
title PromptEar — Setup

echo ========================================
echo   PromptEar v0.10.0 — Установка
echo ========================================
echo.
echo Будет выполнено:
echo   1. Проверка/установка Python 3.12
echo   2. Создание виртуального окружения
echo   3. Установка библиотек (torch, flask и др.)
echo   4. Проверка FFmpeg
echo   5. Проверка/установка Ollama + модель Qwen
echo.
echo Это может занять несколько минут.
echo.

call "%~dp0bootstrap.bat"

echo.
echo ========================================
echo   Установка завершена!
echo   Запустите run.bat для старта.
echo ========================================
pause
