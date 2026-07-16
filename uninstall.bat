@echo off
chcp 65001 >nul
title Удаление PromptEar

echo.
echo === Удаление PromptEar ===
echo.
echo Будут удалены:
echo   - Программа PromptEar (папка с программой)
echo   - Модели Whisper (кеш)
echo.
echo Ollama и модель Qwen останутся (могут использоваться другими программами).
echo.

set /p CONFIRM="Продолжить удаление? (Д/Н): "
if /i not "%CONFIRM%"=="Д" (
    echo Отменено.
    pause
    exit /b 0
)

:: Закрываем PromptEar
taskkill /f /im PromptEar.exe 2>nul

:: Запускаем деинсталлятор Inno Setup
if exist "%~dp0unins000.exe" (
    start /wait "" "%~dp0unins000.exe"
) else (
    echo Файл деинсталлятора не найден.
    echo Вы можете удалить папку вручную: %~dp0
    pause
)
