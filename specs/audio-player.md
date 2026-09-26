# Спека: аудиоплеер в подвале окна «Документ»

Дата: 2026-09-26. Статус: согласовано (решения юзера зафиксированы в §3).

## 1. Цель

Дать возможность слушать исходное аудио прямо в окне «Документ»:

- в подвале панели документа — панель управления: **плэй / пауза / стоп**,
  полоса-таймлайн с текущим временем, проигрывание по клику в текст;
- клик по тексту стенограммы → проигрывание с того места, по которому кликнули
  (старт с начала абзаца, на который пришёлся клик);
- подсветка произносимого абзаца + автопрокрутка за ним.

## 2. Текущее состояние (факты из кода)

- `AudioFile.temp_path` (`core/models.py:15`) — **извлечённый/пропущенный через
  препроцессинг WAV** (16 кГц, моно) — это ровно тот файл, который подавался в
  Whisper. Таймкоды `[MM:SS]` в стенограмме привязаны именно к нему.
- Аудио живёт до конца обработки: `finally` в `_process_files`
  (`web/server.py:313-327`) удаляет `uploaded_files` и весь `TEMP_DIR/task_id`
  (`shutil.rmtree`). После транскрибации аудио на диске **не остаётся**.
  → Нужно публиковать копию WAV в кэш **до** этого `finally`.
- `SaveStep.process` (`core/pipeline.py:229-278`) — шаг, который известен про
  каждый готовый результат `result.audio.temp_path` и выполняется per-file
  (не дожидаясь конца всей задачи). Это точка публикации аудио в кэш.
- `task["results"]` = `{str(audio.path): TranscriptionResult}` (результ-стор);
  `task["display_names"]`, `task["temp_wavs"]`, `task["uploaded_files"]`,
  `task["timestamps"]` (`web/server.py:505-537`).
- `GET /api/results/<task_id>` (`web/server.py:608-629`) возвращает
  `[{filename, text}]` — сюда добавляется признак наличия аудио.
- Окно Документ: `#live-container` (`web/index.html`, `web/style.css:642`),
  внутри `.live-header` + `#result-text` (`style.css:838`). Подвала нет.
- Текст рисует `renderResultText(text)` (`web/script.js:1428-1434`):
  `innerHTML` + замена `[MM:SS]` на `<span class="ts-badge">` по
  `TS_BADGE_RE = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g` (`script.js:1417`).
  Вызывается на каждый чанк стриминга (`setStreamingText`).
- Переключение файла: `selectFile(name)` (`script.js:897`),
  признак «есть результат» — `syncResultActions()` (`script.js:915`).
  Удаление строки из окна «Файлы»: `removeFile(idx)` (`script.js:686`) —
  естественный хук для чистки кэша аудио.
- Один `<audio>` на район: WebView2 умеет WAV; для перемотки нужен
  HTTP-ответ **206 (Range)** — реализуем вручную на Flask/Waitress.

## 3. Согласованные решения

1. **Источник**: извлечённый WAV — `AudioFile.temp_path` (он же был на входе
   Whisper → метки совпадают) ИЛИ `preprocessed_path`, если обработка была.
   Не «исходник», не «оба».
2. **Хранение**: сессионный кэш `data/audio_cache/<task_id>/`; аудио живёт,
   пока не удалена соответствующая строка файла из окна «Файлы»; кэш других
   задач удаляется при новом запуске, весь кэш — при старте приложения.
3. **Точность клика**: по абзацу (клик в середину абзаца → старт от его
   `[MM:SS]`). Посегментная точность — на будущее (Фаза 2).
4. **Синхронизация**: подсветка активного абзаца + автопрокрутка.
5. Бонус-правило корректности: проигрываем **именно** файл, поданный в
   Whisper (`temp_path`), чтобы таймкоды не «плыли» относительно звука.

## 4. Backend

### 4.1 Кэш аудио

- `config.py`: `AUDIO_CACHE_DIR: Final[Path] = DATA_DIR / "audio_cache"`.
- **Публикация** — в `SaveStep.process`: после записи результата
  (`result.output_path`), если есть `result.audio.temp_path`, скопировать его
  в `AUDIO_CACHE_DIR/task_id/`. Только в этот момент обновить
  `result`/результат-стор, чтобы `/api/results` знал про `audio_ok`.
  Задача: `SaveStep` не знает `task_id` — прокидывать через конструктор
  (`SaveStep(result_store, cache_dir=None)`), а конфиг из `run_pipeline_core`
  (`core/pipeline.py:458`) получает `audio_cache_dir` из задачи.
- Имени файла в кэше не доверяем как пути: хранить отображение
  `task["audio_cache"]: dict[str, Path]` = `{display_name: cached_path}`
  (явная мапа вместо коллизий/спецсимволов).
- **Чистка**:
  - `removeFile(name)` → `POST /api/audio/remove/<task_id>/<filename>`
    (удаляет запись из `task["audio_cache"]` и сам файл);
  - новый запуск `/api/run` → удаляет каталоги `AUDIO_CACHE_DIR/*` кроме
    `task_id` текущей задачи;
  - старт приложения → `AUDIO_CACHE_DIR` выметается целиком (в `main.py`).

### 4.2 Раздача (Range)

`GET /api/audio/<task_id>/<filename>`:

- 404, если файла с таким `display_name` в `task["audio_cache"]` нет;
- раздача файла с поддержкой **HTTP Range**:
  - `Accept-Ranges: bytes` всегда;
  - запрос `Range: bytes=a-b` → `206` + `Content-Range` (a, b, размер), тело —
    только диапазон; `bytes=a-` → до конца;
  - `If-Range` и `If-None-Match`/`If-Modified-Since` в MVP можно игнорировать
    (отдаём 200 по absent-range), но `Range` обязателен: без него HTML5
    `<audio>` докачивает весь файл и перемотка глючит.
- стримить по кускам с диска, не держать файл в памяти целиком.

### 4.3 Признак аудио в результатах

`/api/results` для каждого результата добавляет `"audio_ok": bool`
(есть ли запись в `task["audio_cache"]` по display_name).

## 5. Frontend

### 5.1 Разметка подвала

Под `#result-text` внутри `#live-container`:
`index.html` → `<div id="audio-player" style="display:none">`:

```html
<div id="audio-player" class="live-footer">
  <button id="ap-play" onclick="audioToggle()" title="Плэй/Пауза">
    <svg id="ap-play-icon"><path play/></svg>
  </button>
  <button id="ap-stop" onclick="audioStop()" title="Стоп">
    <svg id="ap-stop-icon"><path stop/></svg>
  </button>
  <span id="ap-cur">0:00</span>
  <input id="ap-slider" type="range" min="0" max="0" value="0"
         step="1" oninput="audioSeek(this.value)" />
  <span id="ap-dur">0:00</span>
</div>
```

(`#audio-player.hidden`/й `style.display:none`, когда у текущего файла нет
аудио или нет результата; CSS — токены темы, паттерн `.live-actions button`
из `style.css:739`.)

### 5.2 JS: плеер-синглтон

- Один `const audioEl = new Audio()` (не в DOM), `audioEl.preload="auto"`.
- `selectFile(name)` / `syncResultActions()`: если у файла есть результат и
  `audio_ok` → показать плеер, `audioEl.src =
  /api/audio/${taskId}/${encodeURIComponent(name)}`, остановить/сбросить
  состояние и `tsIndex`.
- Кнопки: **play/pause** — toggle `audioEl.play()/pause()`, иконка
  меняется; **стоп** — `pause()` + `audioEl.currentTime = 0` + сброс слайдера
  и активной строки.
- События: `loadedmetadata` → `slider.max = duration`, `ap-dur`; `timeupdate`
  → `slider.value`, `ap-cur` (`MM:SS`), вызов `syncActiveParagraph()`;
  `ended` → сброс в «плэй»-состояние; `error` → убрать активность, лог в `addLog`.
- Слайдер: `input` → `audioEl.currentTime = v`.

### 5.3 Клик по тексту → старт

- В `renderResultText` после `innerHTML` перестроить
  `tsIndex = [{seconds, el}]` — обойти все `.ts-badge`, распарсить
  `MM:SS`/`HH:MM:SS`. Перестройка стоит дёшево (она и так на каждый чанк
  рисует весь текст).
- Делегированный `click` на `#result-text`:
  - таргет — `.ts-badge` → `seconds` этого бейджа;
  - иначе — найти ближайший **предыдущий** бейдж в document order и взять его
    `seconds`; если бейджей нет (таймкоды выключены) → `0`;
  - `audioSeekTo(seconds)`: если `audioEl.readyState < 2` — дождаться
    `loadedmetadata`, затем `currentTime = seconds; play()`.
- Проигрывание доступно только когда `audio_ok`; иначе клик не активен.

### 5.4 Подсветка абзаца + автопрокрутка

- `syncActiveParagraph()` (на `timeupdate`, троттлинг ~200 мс): бинарный
  поиск в `tsIndex` последнего `seconds <= audioEl.currentTime`, снимает
  `active` со старого, вешает `new .ts-badge.active`
  (`#result-text .ts-badge.active { background: var(--md-primary); color: … }`,
  CSS у `style.css:851`).
- Автопрокрутка: `badgeEl.scrollIntoView({block:'nearest', behavior:'smooth'})`
  только когда есть смена активного бейджа и пользователь не крутил
  `#result-text` последние ~2 с (простой флаг `lastUserScroll`).

### 5.5 Крайние случаи

- **Улучшенный текст**: бейджи сохраняются (`TS_KEEP_RULE`), `tsIndex`
  строится по ним же → работает. **Перевод** без меток → клик = старт с 0.
- **Аудио появляется после `SaveStep`** (результат `done`): до этого плеер
  скрыт; слушатель событий `result`/`files` обновляет `audio_ok`.
- **Переключение файла во время игры**: `selectFile` — старый `src` сброшен,
  стоп, `tsIndex` от нового файла.
- **Удаление файла из «Файлы» во время игры**: `removeFile` → стоп + скрыть
  плеер + `POST /api/audio/remove/…` на бэке.
- **Skipped/пустой результат**: `audio_ok=false`, плеер не показывается
  (аудио может и быть, но документ не готов).
- **Большой WAV + Range**: HTML5-аудио докачивает по диапазонам — память не
  страдает; перемотка мгновенная.

## 6. Объём работ по фазам

**Фаза 1 (реализуем):**
- бэкенд: `AUDIO_CACHE_DIR`, публикация в `SaveStep`, мапа
  `task["audio_cache"]`, `GET /api/audio` c Range, `audio_ok` в `/api/results`,
  чистки (removeFile / новый запуск / старт);
- фронт: подвал-плеер (+CSS), синглтон `AudioEl`, `tsIndex`,
  click→seek по абзацу, подсветка `.ts-badge.active`, автопрокрутка.

**Фаза 2 (потом, в себе спеку)**
- посегментная точность (времена сегментов внутри абзаца, сплиты при рендере);
- кнопки скорости (1×/1.5×/2×), громкость, m Pause клавиши (Space);
- постоянный архив по исходнику (переживает перезапуск), если захочет юзер.

## 7. Файлы для изменения

- `web/server.py` — Range-роут `/api/audio`, `audio_ok`, чистки кэша;
- `core/pipeline.py` — `SaveStep`: публикация WAV в кэш;
- `config.py` — `AUDIO_CACHE_DIR`;
- `main.py` — стартовая чистка кэша;
- `web/index.html` — подвал `#audio-player`;
- `web/script.js` — плеер, `tsIndex`, click→seek, подсветка, автопрокрутка,
  хуки в `selectFile`/`syncResultActions`/`removeFile`/`renderResultText`;
- `web/style.css` — `.live-footer` и `.ts-badge.active`.

## 8. Проверка

Без pytest-сьюты (её в репо нет): ruff/mypy/py_compile на изменённых `.py`,
`node --check web/script.js`, ручной чеклист в UI:

1. Запуск задачи с таймкодами → после `done` в подвале плеер с правильной
   длительностью;
2. клик по бейджу → старт с его времени; клик в середину абзаца → старт с
   начала абзаца; клик до первого бейджа → с 0;
3. слайдер перематывает; стоп сбрасывает; переключение файла — свой src;
4. подсветка и автопрокрутка следят за звуком;
5. `curl -H "Range: bytes=0-1023" /api/audio/…` → `206` + `Content-Range`;
6. удаление файла из окна «Файлы» убирает аудио из кэша и глушит плеер.