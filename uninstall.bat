@echo off
chcp 65001 >nul
title Удаление PromptEar

echo.
echo === Удаление PromptEar ===
echo.
echo Будут удалены:
echo   - PromptEar (папка с программой)
echo   - Модели Whisper (кеш)
echo.
echo Ollama и модель Qwen останутся (могут использоваться другими программами).
echo.
echo Виртуальное окружение (venv) будет удалено вместе с программой.
echo.
echo Продолжить? (Д/Н)
set /p CONFIRM=

if /i "%CONFIRM%" neq "Д" (
    echo Отменено.
    pause
    exit /b 0
)

:: Закрываем Python (PromptEar)
echo Закрытие PromptEar...
taskkill /f /im python.exe 2>nul

:: Деинсталлятор Inno Setup
if exist "%~dp0unins000.exe" (
    echo Запуск деинсталлятора...
    start /wait "" "%~dp0unins000.exe"
) else (
    echo Деинсталлятор не найден — удаляем вручную...
    :: Удаляем venv
    if exist "%~dp0venv" (
        rmdir /s /q "%~dp0venv"
    )
    :: Удаляем кеш Whisper
    if exist "%USERPROFILE%\.cache\whisper" (
        rmdir /s /q "%USERPROFILE%\.cache\whisper"
    )
    :: Удаляем логи и настройки
    if exist "%APPDATA%\PromptEar" (
        rmdir /s /q "%APPDATA%\PromptEar"
    )
)
echo.
echo PromptEar удалён.
pause
