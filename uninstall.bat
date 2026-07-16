@echo off
chcp 65001 >nul
title Удаление PromptEar

set "APP_DIR=%~dp0"
set "SILENT="
if "%1"=="--silent" set "SILENT=1"

:: ── Выбор компонентов ──────────────────────────────────────────────────
set "DO_PROGRAM=1"
set "DO_CACHE=1"
set "DO_OLLAMA="
set "DO_MODEL="
set "DO_SETTINGS=1"

if not defined SILENT (
    cls
    echo.
    echo === Удаление PromptEar ===
    echo.
    echo Выберите, что удалить (через пробел, Enter = по умолчанию):
    echo.
    echo   1 — PromptEar (папка программы + venv)          [ДА]
    echo   2 — Кеш Whisper (%%USERPROFILE%%\.cache\whisper) [ДА]
    echo   3 — Ollama (системная программа)                 [НЕТ]
    echo   4 — Модель Qwen 2.5 3b (ollama rm)              [НЕТ]
    echo   5 — Настройки и логи (%%APPDATA%%\PromptEar)    [ДА]
    echo.
    echo   0 — Выход без удаления
    echo.
    set /p CHOICE="Номера через пробел [1 2 5]: "

    if "%CHOICE%"=="0" (
        echo Отменено.
        pause
        exit /b 0
    )

    if not "%CHOICE%"=="" (
        set "DO_PROGRAM="
        set "DO_CACHE="
        set "DO_OLLAMA="
        set "DO_MODEL="
        set "DO_SETTINGS="
        for %%n in (%CHOICE%) do (
            if "%%n"=="1" set "DO_PROGRAM=1"
            if "%%n"=="2" set "DO_CACHE=1"
            if "%%n"=="3" set "DO_OLLAMA=1"
            if "%%n"=="4" set "DO_MODEL=1"
            if "%%n"=="5" set "DO_SETTINGS=1"
        )
    )
)

echo.
echo Удаление...

:: ── 1. Программа + venv ─────────────────────────────────────────────
if defined DO_PROGRAM (
    echo   [1] Удаление PromptEar...
    taskkill /f /im python.exe >nul 2>&1

    if exist "%APP_DIR%venv" (
        rmdir /s /q "%APP_DIR%venv"
    )
    :: Папку программы удалит Inno Setup самостоятельно
    if not exist "%APP_DIR%unins000.exe" (
        :: Режим без Inno Setup — удаляем всё сами
        if exist "%APP_DIR%ui" rmdir /s /q "%APP_DIR%ui"
        if exist "%APP_DIR%processing" rmdir /s /q "%APP_DIR%processing"
        if exist "%APP_DIR%utils" rmdir /s /q "%APP_DIR%utils"
        del /f /q "%APP_DIR%main.py" >nul 2>&1
        del /f /q "%APP_DIR%config.py" >nul 2>&1
        del /f /q "%APP_DIR%app.py" >nul 2>&1
        del /f /q "%APP_DIR%run.bat" >nul 2>&1
        del /f /q "%APP_DIR%requirements.txt" >nul 2>&1
        del /f /q "%APP_DIR%PromptEar.exe" >nul 2>&1
        rmdir "%APP_DIR%" >nul 2>&1
    )
) else (
    echo   [1] Пропущено
)

:: ── 2. Кеш Whisper ──────────────────────────────────────────────────
if defined DO_CACHE (
    echo   [2] Удаление кеша Whisper...
    if exist "%USERPROFILE%\.cache\whisper" (
        rmdir /s /q "%USERPROFILE%\.cache\whisper"
    )
) else (
    echo   [2] Пропущено
)

:: ── 3. Ollama ───────────────────────────────────────────────────────
if defined DO_OLLAMA (
    echo   [3] Удаление Ollama...
    if exist "C:\Program Files\Ollama\Uninstall.exe" (
        start /wait "" "C:\Program Files\Ollama\Uninstall.exe" /S
    ) else if exist "%LOCALAPPDATA%\Ollama\Ollama.exe" (
        start /wait "" "%LOCALAPPDATA%\Ollama\Ollama.exe" /uninstall
    ) else (
        echo    Ollama не найдена — пропущено
    )
) else (
    echo   [3] Пропущено
)

:: ── 4. Модель Qwen ──────────────────────────────────────────────────
if defined DO_MODEL (
    echo   [4] Удаление модели Qwen 2.5 3b...
    where ollama >nul 2>&1
    if %errorlevel% equ 0 (
        ollama rm qwen2.5:3b
    ) else (
        echo    Ollama не запущена — пропущено
    )
) else (
    echo   [4] Пропущено
)

:: ── 5. Настройки и логи ─────────────────────────────────────────────
if defined DO_SETTINGS (
    echo   [5] Удаление настроек и логов...
    if exist "%APPDATA%\PromptEar" (
        rmdir /s /q "%APPDATA%\PromptEar"
    )
) else (
    echo   [5] Пропущено
)

:: ── Готово ──────────────────────────────────────────────────────────
echo.
echo === Удаление завершено ===
if defined SILENT (
    exit /b 0
)
pause
