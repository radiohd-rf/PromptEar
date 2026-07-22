# README + SEO — Spec v0.1

## Цель

Улучшить README из технической инструкции в "витрину" проекта.  
Заполнить метаданные репозитория для поиска на GitHub.

---

## 1. README — новая структура

```
# PromptEar  [бейджи]

> 🔥 Устали вручную расшифровывать интервью или переслушивать лекции?
> PromptEar делает это за вас — полностью локально, за пару кликов.

## 📸 Демонстрация
[скриншот интерфейса]

## 🎯 Для кого
- **Журналисты / блогеры** — вместо часов расшифровки — пара минут
- **Студенты** — лекции превращаются в структурированные конспекты
- **Разработчики** — стенограммы созвонов и митапов
- **Все** — забудьте о печати с голоса

## ✨ Возможности
(лаконичный список с иконками)

## ⚡ Быстрый старт (CPU)
(актуальные ссылки на v0.11.1)

## 🚀 GPU версия (CUDA)
(ссылка на Яндекс.Диск, пароль)

## 🏗️ Архитектура
(компактная схема)

## 🔧 Сборка из исходников

## 📄 Лицензия (MIT)
```

### 1.1 Бейджи (shields.io)

```markdown
![Version](https://img.shields.io/badge/version-0.11.1-blue)
![Platform](https://img.shields.io/badge/platform-Windows-0078d7)
![License](https://img.shields.io/badge/license-MIT-green)
![Privacy](https://img.shields.io/badge/privacy-100%25_local-brightgreen)
```

### 1.2 Скриншот

- Файл: `assets/screenshot.png`
- Показать: окно приложения с загруженными файлами + лог обработки
- Сделать скриншот вручную через Windows (не автоматизировать)

### 1.3 Тон

- Дружественный, решает проблему пользователя
- Минимум технических деталей в начале
- Техническая часть — ниже (архитектура, сборка)

---

## 2. Метаданные репозитория

Выполнить через `gh repo edit`:

### 2.1 Description (About)

```
Local audio/video transcription with AI text enhancement using faster-whisper + Ollama (Qwen 2.5). Windows desktop app.
```

### 2.2 Topics

```
audio-transcription, whisper, video-transcription, ollama, local-ai, windows-app, speech-to-text, qwen, transcriber, productivity
```

---

## 3. Файлы для изменения

| Файл | Действие |
|------|----------|
| `README.md` | полный переписать |
| `assets/screenshot.png` | **создать** (скриншот вручную) |
| _(repo settings)_ | `gh repo edit` — description + topics |

---

## 4. Версия

- v0.11.1
