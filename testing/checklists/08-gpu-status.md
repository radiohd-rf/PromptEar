# Чек-лист: GPU и статус-бар

## 🛠️ Чем проверять

**curl (cmd / Git Bash) — проверить API:**
curl http://127.0.0.1:5000/api/gpu
curl http://127.0.0.1:5000/api/ollama
В ответе должен быть JSON. Если HTML — что-то не так.

**nvidia-smi (если есть NVIDIA):**
просто напиши nvidia-smi в cmd — увидишь список GPU, температуру, память. Если нет такой команды — драйвера NVIDIA не стоят.

**Python — CUDA:**
python -c "import torch; print(torch.cuda.is_available())"
True — всё ок, CUDA работает. False — только CPU.

**Ollama:**
ollama list
Покажет установленные модели. Если qwen2.5:3b нет — модель не скачана.

**DevTools (F12 через браузер):**
Elements — найди футер (footer или div внизу). Там строка "GPU: ... | Torch ... | Qwen ...". Смотри цвет:
- зелёный — CUDA доступна
- обычный — нет

**Process Explorer:**
найди ollama.exe в списке процессов. Если есть — Ollama запущена.

## Техники: Таблица принятия решений (Decision Table)

### Decision Table: статус-бар

Условия:
- A: nvidia-smi найден
- B: torch.cuda.is_available()
- C: Ollama установлен
- D: qwen2.5:3b в списке

| # | A | B | C | D | Статус-бар |
|---|----|----|----|----|------------|
| 1 | Y | Y | Y | Y | GPU: NVIDIA ... | Torch CUDA | Qwen ✅ |
| 2 | Y | N | Y | Y | GPU: NVIDIA ... | Torch CPU | Qwen ✅ |
| 3 | N | N | Y | Y | GPU: не обнаружена | Torch CPU | Qwen ✅ |
| 4 | Y | Y | N | N | GPU: NVIDIA ... | Torch CUDA | Ollama: не обнаружен |
| 5 | Y | Y | Y | N | GPU: NVIDIA ... | Torch CUDA | Qwen: ⚠ модель не найдена |
| 6 | N | N | N | N | GPU: не обнаружена | Torch CPU | Ollama: не обнаружен |

- [ ] Все 6 комбинаций дают правильный статус-бар в футере
- [ ] Цвет: зелёный (--success) когда CUDA доступна
- [ ] Серый/обычный — когда CUDA нет

### Проверки GPU

- [ ] Статус-бар отображается при загрузке страницы
- [ ] Запрос к /api/gpu возвращает корректный JSON
- [ ] Запрос к /api/ollama возвращает корректный JSON
- [ ] Обновление статуса происходит асинхронно (не блокирует UI)
