@echo off
title PromptEar Uninstall

set "APP_DIR=%~dp0"
set "SILENT="
if "%1"=="--silent" set "SILENT=1"

:: ── Выбор компонентов ──────────────────────────────────────────────────
set "DO_PROGRAM=1"
set "DO_CACHE=1"
set "DO_MODELS="
set "DO_LLAMA="
set "DO_SETTINGS=1"

if not defined SILENT (
    cls
    echo.
    echo === Удаление PromptEar ===
    echo.
    echo Выберите, что удалить (через пробел, Enter = по умолчанию):
    echo.
    echo   1 — PromptEar (папка программы + venv)          [ДА]
    echo   2 — Кеш Whisper (%%APP_DIR%%\models\hf)         [ДА]
    echo   3 — Модели LLM (%%APP_DIR%%\models\llm, sage)   [НЕТ]
    echo   4 — llama.cpp (%%APP_DIR%%\llama)                [НЕТ]
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
        set "DO_MODELS="
        set "DO_LLAMA="
        set "DO_SETTINGS="
        for %%n in (%CHOICE%) do (
            if "%%n"=="1" set "DO_PROGRAM=1"
            if "%%n"=="2" set "DO_CACHE=1"
            if "%%n"=="3" set "DO_MODELS=1"
            if "%%n"=="4" set "DO_LLAMA=1"
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
        if exist "%APP_DIR%core" rmdir /s /q "%APP_DIR%core"
        if exist "%APP_DIR%models" rmdir /s /q "%APP_DIR%models"
        if exist "%APP_DIR%llama" rmdir /s /q "%APP_DIR%llama"
        if exist "%APP_DIR%web" rmdir /s /q "%APP_DIR%web"
        rmdir "%APP_DIR%" >nul 2>&1
    )
) else (
    echo   [1] Пропущено
)

:: ── 2. Кеш (HF/whisper + ct2) ────────────────────────────────────────
if defined DO_CACHE (
    echo   [2] Удаление кеша моделей...
    if exist "%APP_DIR%models\hf" rmdir /s /q "%APP_DIR%models\hf"
    if exist "%APP_DIR%models\ct2" rmdir /s /q "%APP_DIR%models\ct2"
) else (
    echo   [2] Пропущено
)

:: ── 3. Модели LLM ────────────────────────────────────────────────────
if defined DO_MODELS (
    echo   [3] Удаление моделей LLM...
    if exist "%APP_DIR%models\llm" rmdir /s /q "%APP_DIR%models\llm"
    if exist "%APP_DIR%models\sage" rmdir /s /q "%APP_DIR%models\sage"
) else (
    echo   [3] Пропущено
)

:: ── 4. llama.cpp ─────────────────────────────────────────────────────
if defined DO_LLAMA (
    echo   [4] Удаление llama.cpp...
    taskkill /f /im llama-server.exe >nul 2>&1
    if exist "%APP_DIR%llama" rmdir /s /q "%APP_DIR%llama"
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
