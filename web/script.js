/* PromptEar web UI — клиентская часть (v0.15) */

let files = [];
let taskId = null;
let eventSource = null;
let isDark = null;            // null = системная тема; true/false — переопределено юзером
let enhanceMode = 'auto';
let currentFileName = null;
let liveFile = null;
const fileStatuses = {};   // имя -> status
const fileTexts = {};      // имя -> последний текст (черновик/улучшенный)
const fileBadges = {};     // имя -> бейдж
const statusLabels = {
  queued: 'В очереди',
  processing: 'Подготовка',
  transcribing: 'В обработке',
  enhancing: 'Улучшение ИИ',
  done: 'Готово',
  skipped: 'Пропущено',
};

/* ── Материальная палитра (Material You) ─────────────────── */

const THEME_ATTR = 'data-theme';

function applyThemeTokens(t) {
  document.documentElement.setAttribute(THEME_ATTR, t.theme);
  for (const [k, v] of Object.entries(t.tokens)) {
    document.documentElement.style.setProperty(k, v);
  }
}

async function applyPalette(darkOverride) {
  let url = '/api/theme';
  if (darkOverride !== undefined && darkOverride !== null) {
    url += `?dark=${darkOverride ? '1' : '0'}`;
  }
  try {
    const t = await (await fetch(url)).json();
    applyThemeTokens(t);
    return t.dark;
  } catch (_) {
    // оффлайн/сервер недоступен — фолбэк-токены из style.css
    return document.documentElement.getAttribute(THEME_ATTR) === 'dark';
  }
}

function preferredTheme() {
  const saved = localStorage.getItem('promptear-theme');
  return saved === 'dark' || saved === 'light' ? saved : null;
}

function initTheme() {
  const saved = preferredTheme();
  if (saved) {
    document.documentElement.setAttribute('style', '');  // сбросить inline-токены
    applyPalette(saved === 'dark').then(d => { isDark = d; });
  } else {
    applyPalette().then(d => { isDark = d; });
  }
}

// Смена темы/обоев Windows: поллинг системной палитры (focus в WebView2
// ненадёжен). Применяем токены только при реальном изменении сигнатуры.
let lastThemeSig = '';

async function pollTheme() {
  const saved = preferredTheme();
  const forced = saved !== null;
  const url = '/api/theme' + (forced ? `?dark=${saved === 'dark' ? '1' : '0'}` : '');
  try {
    const t = await (await fetch(url)).json();
    const sig = t.theme + '|' + t.seed + (forced ? '|forced' : '');
    if (sig !== lastThemeSig) {
      lastThemeSig = sig;
      applyThemeTokens(t);
      isDark = t.dark;
    }
  } catch (_) {
    // сеть недоступна — тихо пропускаем
  }
}

setInterval(pollTheme, 3000);

async function toggleTheme() {
  const next = !isDark;
  localStorage.setItem('promptear-theme', next ? 'dark' : 'light');
  lastThemeSig = '';
  const t = await applyPalette(next);
  isDark = t;
}

/* ── Утилиты ─────────────────────────────────────────────── */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function escapeJs(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
}

function formatSize(n) {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' МБ';
  if (n > 1024) return (n / 1024).toFixed(0) + ' КБ';
  return n + ' Б';
}

/* ── Drag & Drop ─────────────────────────────────────────── */

function onDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('dragover');
  addFiles(e.dataTransfer.files);
}

function onFileSelect(e) {
  addFiles(e.target.files);
  e.target.value = '';
}

function addFiles(fileList) {
  for (const f of fileList) {
    if (!files.find(x => x.name === f.name && x.size === f.size)) {
      files.push(f);
      fileStatuses[f.name] = 'queued';
      fileTexts[f.name] = '';
      fileBadges[f.name] = 'Черновик';
    }
  }
  renderFileList();
}

function removeFile(idx) {
  fileStatuses[files[idx].name] = undefined;
  delete fileTexts[files[idx].name];
  delete fileBadges[files[idx].name];
  files.splice(idx, 1);
  renderFileList();
}

function renderFileList() {
  const list = document.getElementById('file-list');

  if (files.length === 0) {
    document.getElementById('drop-text').textContent = 'Перетащите аудиофайлы сюда';
    list.innerHTML = '';
  } else {
    document.getElementById('drop-text').textContent = 'Перетащите ещё файлы';
  }

  list.innerHTML = files.map((f, i) => {
    const st = fileStatuses[f.name] || 'queued';
    const clickable = st !== 'queued';
    const removable = !fileStatuses[f.name] || st === 'queued';
    const selected = f.name === currentFileName && clickable;
    return `<li class="file-item ${clickable ? 'clickable' : ''} ${selected ? 'selected' : ''}"
      data-name="${escapeHtml(f.name)}" onclick="selectFile('${escapeJs(f.name)}')">
      <span class="file-num">${i + 1}.</span>
      <span class="file-name">${escapeHtml(f.name)}</span>
      <span class="file-size">${formatSize(f.size)}</span>
      <span class="status status-${st}">${statusLabels[st] || 'В очереди'}</span>
      ${removable ? `<span class="remove" onclick="event.stopPropagation(); removeFile(${i})">✕</span>` : ''}
    </li>`;
  }).join('');
}

function selectFile(name) {
  if (!fileStatuses[name] || fileStatuses[name] === 'queued') return;
  currentFileName = name;
  renderFileList();
  const text = fileTexts[name] || '';
  const improved = fileBadges[name] === 'Улучшено ИИ';
  setResultText(text, improved);
}

/* ── Загрузка и обработка ────────────────────────────────── */

async function runPipeline() {
  if (files.length === 0) return;

  const formData = new FormData();
  for (const f of files) {
    formData.append('files', f);
  }
  formData.append('output_format', document.getElementById('output-format').value);
  enhanceMode = document.getElementById('enhance-mode').value;
  formData.append('enhance_mode', enhanceMode);

  const ctx = document.getElementById('context-prompt').value.trim();
  if (ctx) formData.append('initial_prompt', ctx);

  setBusy(true);
  clearLog();
  addLog('=== PromptEar ===');
  showSpinner();
  resetResult();
  showLivePanel();

  // сбросить статусы у выбранных файлов
  for (const f of files) {
    fileStatuses[f.name] = 'queued';
    fileTexts[f.name] = '';
    fileBadges[f.name] = 'Черновик';
  }
  renderFileList();

  try {
    const resp = await fetch('/api/files', { method: 'POST', body: formData });
    const data = await resp.json();

    if (data.error) {
      addLog(`Ошибка: ${data.error}`);
      setBusy(false);
      return;
    }

    taskId = data.task_id;
    if (data.enhance_mode) enhanceMode = data.enhance_mode;
    addLog(`Отправлено ${data.file_count} файлов`);
    connectSSE(taskId);
  } catch (err) {
    addLog(`Ошибка: ${err.message}`);
    setBusy(false);
  }
}

function connectSSE(task_id) {
  if (eventSource) eventSource.close();

  eventSource = new EventSource(`/api/status/${task_id}`);

  eventSource.onmessage = (e) => {
    if (e.data === ': keepalive') return;

    try {
      const msg = JSON.parse(e.data);
      handleEvent(msg);
    } catch (_) {
      // не JSON — пропускаем
    }
  };

  eventSource.onerror = () => {
    addLog('SSE: соединение потеряно');
    eventSource.close();
  };
}

function handleEvent(msg) {
  switch (msg.type) {
    case 'log':
      addLog(msg.message);
      break;

    case 'draft':
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Черновик (Whisper)';
      if (msg.final) {
        finishStreaming(msg.text, false);
      } else {
        setStreamingText(msg.text);
        updateBadge('Черновик (Whisper)');
      }
      if (msg.final && enhanceMode === 'ask') {
        showEnhanceButton();
      }
      break;

    case 'enhancing':
      showEnhanceProgress(msg.active_pass, msg.total_passes);
      if (msg.active_pass === 1) {
        // Проход 1 начался: черновик whisper должен быть допечатан.
        // Если whisper уже доложил весь текст, но печать не успела —
        // плавно добираем оставшееся, а не показываем резко.
        const el = document.getElementById('result-text');
        const draft = fileTexts[liveFile] || '';
        if (draft && typingStarted && typeTarget && draft.length > Math.max(typeShownLen, el.textContent.length)) {
          typeTarget = draft;
          typeFinishing = true;
          startTypingTimer();
        } else {
          resetTyping();
          el.textContent = draft;
          el.scrollTop = el.scrollHeight;
        }
        updateBadge('Улучшение ИИ');
      } else if (lastPassText != null) {
        // предыдущий проход завершён — его полный текст мы уже получили,
        // плавно переходим: мигание → затухание → появление
        startPassTransition(lastPassText);
      }
      break;

    case 'enhancing_stream':
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Улучшение ИИ';
      // не печатаем по токенам: копим полный текст прохода,
      // он появится целиком на переходе к следующему проходу
      lastPassText = msg.text;
      break;

    case 'result':
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Улучшено ИИ';
      hideEnhanceProgress();
      startPassTransition(msg.text);
      break;

    case 'file_status':
      fileStatuses[msg.filename] = msg.status;
      if (msg.status === 'transcribing') {
        liveFile = msg.filename;
        setLiveFile(liveFile);
        document.getElementById('skip-btn').style.display = '';
      }
      renderFileList();
      break;

    case 'skipped':
      fileStatuses[msg.filename] = 'skipped';
      fileBadges[msg.filename] = fileBadges[msg.filename] || 'Черновик';
      liveFile = currentFileName || msg.filename;
      setLiveFile(liveFile);
      document.getElementById('skip-btn').style.display = 'none';
      renderFileList();
      break;

    case 'progress':
      liveFile = currentFileName || msg.filename;
      setLiveFile(liveFile);
      break;

    case 'transcribing':
      hideEnhanceProgress();
      break;

    case 'busy':
      setBusy(msg.busy);
      break;

    case 'llm_ready':
      if (!msg.llm_ok) {
        addLog('⚠ LLM-движок не найден. Улучшение текста отключено.');
      } else if (!msg.model_ok) {
        addLog('⚠ Модель не найдена. Улучшение может не работать.');
      } else {
        addLog('✅ LLM доступен');
      }
      break;

    case 'done':
      addLog('✅ ' + msg.message);
      addLog('📁 Результаты сохранены в папке "output"');
      document.getElementById('skip-btn').style.display = 'none';
      if (enhanceMode === 'ask') {
        awaitSyncAskResult().then(() => finish());
      } else {
        finish();
      }
      break;

    case 'error':
      addLog('❌ ' + msg.message);
      finish();
      break;

    case 'cancelled':
      addLog('⛔ Отменено');
      finish();
      break;

    case '__done__':
      break;
  }
}

function firstActiveFile() {
  for (const f of files) {
    const st = fileStatuses[f.name];
    if (st && st !== 'queued' && st !== 'done' && st !== 'skipped') return f.name;
  }
  return null;
}

function setLiveFile(name) {
  if (name && name !== liveFile) {
    liveFile = name;
    currentFileName = name;
  }
  renderFileList();
}

function cancelPipeline() {
  if (taskId) {
    fetch(`/api/cancel/${taskId}`, { method: 'POST' });
  }
}

function skipFile() {
  if (taskId && liveFile) {
    fetch(`/api/skip/${taskId}/${encodeURIComponent(liveFile)}`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.error) addLog(`⚠ ${d.error}`);
      });
  }
}

function setBusy(busy) {
  const runBtn = document.getElementById('run-btn');
  runBtn.disabled = busy;
  runBtn.style.display = busy ? 'none' : '';
  document.getElementById('cancel-btn').style.display = busy ? '' : 'none';
}

function finish() {
  hideSpinner();
  if (eventSource) { eventSource.close(); eventSource = null; }
  setBusy(false);
  document.getElementById('skip-btn').style.display = 'none';
  if (enhanceMode !== 'ask') {
    hideEnhanceButton();
    hideEnhanceProgress();
  }
}

/* ── Help modal ─────────────────────────────────────────── */

function openHelp() {
  document.getElementById('help-overlay').style.display = 'flex';
}

function closeHelp() {
  document.getElementById('help-overlay').style.display = 'none';
}

/* ── Печать текста в лайве (плавная, без видимых пауз) ──
   Скорость = средний темп прихода текста с начала потока (стабильный, сам
   уточняется каждым батчем). У конца имеющихся данных печать плавно
   замедляется («тормозит»), растягивая остаток на паузу, пока whisper
   генерирует следующий сегмент — видимых остановок нет. На final событии
   текст показывается мгновенно. */

let typeTimer = null;
let typeTarget = null;
let typeShownLen = 0;     // сколько символов уже показано (дробное)
let emaRate = null;       // chars/ms — средний темп прихода с старта потока
let typingStarted = false;
let streamStartAt = 0;    // момент прихода первого текста
let typeFinishing = false; // пришёл final — добираем оставшийся хвост быстро, но плавно
let pendingText = null;    // следующий проход геммы ждёт, пока текущий допечатается
let pendingPass = null;    // «Проход N/M» ждёт, пока текущий текст допечатается
let lastPassText = null;   // полный текст последнего полученного прохода геммы

const TYPE_TICK_MS = 16;   // тик ~60 Гц
const BASE_RATE = 0.014;   // chars/ms пока нет данных — печатная скорость ~14 с/с
const MAX_RATE = 0.03;     // верхняя граница (30 симв/с)
const FINISH_DURATION_MS = 400; // за сколько добирать хвост при смене прохода (~0.4 сек)

function setStreamingText(text) {
  const el = document.getElementById('result-text');
  const shown = el.textContent;
  // поток нарастает — это продолжение текущей выдачи
  if (typeTarget && text.startsWith(shown) && text.length >= shown.length) {
    const now = performance.now();
    typeTarget = text;
    if (!typingStarted) {
      // первый батч: начало потока, печатаем сразу с базовой скоростью
      typingStarted = true;
      streamStartAt = now;
      typeShownLen = 0;
      startTypingTimer();
      return;
    }
    // уточняем средний темп по всему потоку (не дёргается от пауз между сегментами)
    const elapsed = now - streamStartAt;
    if (elapsed > 1500) {
      emaRate = Math.max(BASE_RATE, Math.min(MAX_RATE, text.length / elapsed));
    }
    if (typeShownLen < text.length && !typeTimer) startTypingTimer();
    return;
  }
  // новый поток (или текст сброшен) — начинаем сначала.
  // Но если печатается предыдущая версия и это новый проход геммы —
  // добираем текущую быстро (finishing), новую печатаем после.
  if (pendingText) {
    pendingText = text;
    return;
  }
  if (typingStarted && typeTarget && typeShownLen < typeTarget.length) {
    pendingText = text;
    typeFinishing = true;
    startTypingTimer();
    return;
  }
  resetTyping();
  el.textContent = '';
  if (text.length > 0) {
    typeTarget = text;
    typingStarted = true;
    streamStartAt = performance.now();
    typeShownLen = 0;
    startTypingTimer();
  }
}

function startTypingTimer() {
  stopTyping();
  typeTimer = setInterval(typeTick, TYPE_TICK_MS);
}

function typeTick() {
  const tgt = typeTarget;
  if (!tgt) { stopTyping(); return; }
  let rate = emaRate != null ? emaRate : BASE_RATE;
  const backlog = tgt.length - typeShownLen;
  if (typeFinishing) {
    // смена прохода: добираем оставшееся за ~0.4 сек независимо от размера
    if (backlog > 1) rate = Math.min(2.0, Math.max(BASE_RATE, backlog / FINISH_DURATION_MS));
  }
  typeShownLen = Math.min(tgt.length, typeShownLen + rate * TYPE_TICK_MS);
  const el = document.getElementById('result-text');
  el.textContent = tgt.slice(0, Math.floor(typeShownLen));
  el.scrollTop = el.scrollHeight;
  if (typeShownLen >= tgt.length) {
    stopTyping();
    if (pendingPass) renderEnhanceProgress(pendingPass.active, pendingPass.total);
    if (pendingText) {
      // предыдущий проход добран до конца — печатаем следующий проход геммы
      const next = pendingText;
      pendingText = null;
      typeTarget = next;
      typeShownLen = 0;
      typeFinishing = false;
      emaRate = null;
      streamStartAt = performance.now();
      startTypingTimer();
    }
  }
}

function resetTyping() {
  stopTyping();
  typeTarget = null;
  typeShownLen = 0;
  emaRate = null;
  typingStarted = false;
  streamStartAt = 0;
  typeFinishing = false;
  pendingText = null;
  pendingPass = null;
  lastPassText = null;
  if (passTransitionTimer) {
    clearTimeout(passTransitionTimer);
    passTransitionTimer = null;
  }
  const el = document.getElementById('result-text');
  el.classList.remove('pass-blink', 'pass-fade-out', 'pass-fade-in');
}

function stopTyping() {
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null; }
}

// Приход финального события: печатаем оставшийся хвост равномерно за ~2 сек
// вместо мгновенного скачка (иначе в переходе whisper→gemma всё «допрыгивает»).
function finishStreaming(text, improved) {
  const el = document.getElementById('result-text');
  updateBadge(improved ? 'Улучшено ИИ' : 'Черновик (Whisper)');
  // если печать уже шла и текст просто нарос — плавно добираем хвост
  if (typeTarget && typingStarted && text.length > typeShownLen) {
    typeTarget = text;
    typeFinishing = true;
    startTypingTimer();
    return;
  }
  // иначе (текст не рос, печать не началась) — показать сразу
  resetTyping();
  el.textContent = text;
}

/* ── Live panel ──────────────────────────────────────────── */

function showLivePanel() {
  const el = document.getElementById('live-container');
  el.style.display = 'flex';
}

function resetResult() {
  currentFileName = null;
  liveFile = null;
  hideEnhanceButton();
  hideEnhanceProgress();
  setResultText('', false);
  updateBadge('Черновик');
}

function updateBadge(text) {
  const badge = document.getElementById('result-badge');
  badge.textContent = text || 'Черновик';
  badge.classList.toggle('improved', text === 'Улучшено ИИ');
}

function setResultText(text, improved) {
  resetTyping();
  const el = document.getElementById('result-text');
  el.textContent = text;
  updateBadge(improved ? 'Улучшено ИИ' : (text ? 'Черновик (Whisper)' : 'Черновик'));
}

function showEnhanceProgress(activePass, totalPasses) {
  // номер прохода сервер шлёт в момент старта прохода, раньше его текста —
  // не показываем, пока дописывается предыдущий проход
  if (typingStarted && typeShownLen < (typeTarget ? typeTarget.length : 0)) {
    pendingPass = { active: activePass, total: totalPasses };
    return;
  }
  renderEnhanceProgress(activePass, totalPasses);
}

function renderEnhanceProgress(activePass, totalPasses) {
  pendingPass = null;
  const wrap = document.getElementById('enhance-progress');
  wrap.style.display = 'flex';
  const bar = document.getElementById('enhance-progress-bar');
  bar.style.width = `${Math.round((activePass / totalPasses) * 100)}%`;
  document.getElementById('enhance-progress-label').textContent =
    `Проход ${activePass}/${totalPasses}`;
}

function hideEnhanceProgress() {
  pendingPass = null;
  document.getElementById('enhance-progress').style.display = 'none';
}

/* ── Переход между проходами геммы: мигание → затухание → появление ── */

let passTransitionTimer = null;

function startPassTransition(newText) {
  const el = document.getElementById('result-text');
  resetTyping();
  if (passTransitionTimer) clearTimeout(passTransitionTimer);
  // 1. текущий текст мигает ~5 сек (проход N завершён, обрабатываем результат)
  el.classList.remove('pass-fade-out', 'pass-fade-in');
  el.classList.add('pass-blink');
  passTransitionTimer = setTimeout(() => {
    // 2. затухание: текст на секунду исчезает
    el.classList.remove('pass-blink');
    el.classList.add('pass-fade-out');
    passTransitionTimer = setTimeout(() => {
      // 3. появляется полный текст нового прохода
      el.classList.remove('pass-fade-out');
      el.textContent = newText;
      el.scrollTop = el.scrollHeight;
      el.classList.add('pass-fade-in');
      passTransitionTimer = setTimeout(() => {
        el.classList.remove('pass-fade-in');
        passTransitionTimer = null;
      }, 700);
    }, 1000);
  }, 5000);
}

function showEnhanceButton() {
  document.getElementById('enhance-btn').style.display = '';
}

function hideEnhanceButton() {
  document.getElementById('enhance-btn').style.display = 'none';
}

function copyResult() {
  const text = document.getElementById('result-text').textContent;
  if (!text) return;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => addLog('📋 Текст скопирован'));
  } else {
    addLog('⚠ Копирование недоступно');
  }
}

async function enhanceDraft() {
  if (!taskId || !currentFileName) return;
  const btn = document.getElementById('enhance-btn');
  if (!btn) return;
  btn.disabled = true;
  setBtnLabel(btn, 'Улучшаем…');
  showEnhanceProgress(1, 3);
  try {
    const resp = await fetch(`/api/enhance/${taskId}/${encodeURIComponent(currentFileName)}`, { method: 'POST' });
    const data = await resp.json();
    if (data.error) {
      addLog(`❌ ${data.error}`);
    } else {
      fileTexts[currentFileName] = data.text;
      fileBadges[currentFileName] = 'Улучшено ИИ';
      hideEnhanceProgress();
      setResultText(data.text, true);
      addLog('✅ Текст улучшен и перезаписан');
    }
  } catch (err) {
    addLog(`❌ ${err.message}`);
  } finally {
    btn.disabled = false;
    setBtnLabel(btn, 'Улучшить с ИИ');
    hideEnhanceButton();
  }
}

function setBtnLabel(btn, text) {
  // сохраняем inline-SVG иконку: меняем только текстовый узел
  const icon = btn.querySelector('svg');
  btn.textContent = text;
  if (icon) btn.prepend(icon);
}

async function awaitSyncAskResult() {
  if (!taskId) return;
  try {
    const resp = await fetch(`/api/results/${taskId}`);
    const data = await resp.json();
    const results = data.results || [];
    if (results.length > 0) {
      const first = results[0];
      currentFileName = first.filename;
      fileTexts[currentFileName] = first.text;
      fileBadges[currentFileName] = 'Черновик (Whisper)';
      if (first.text) {
        setResultText(first.text, false);
        showEnhanceButton();
      }
      renderFileList();
    }
  } catch (_) {
    // не критично — черновик уже показан через SSE
  }
}

/* ── UI helpers ──────────────────────────────────────────── */

function showSpinner() {
  document.getElementById('thinking-spinner').style.display = '';
}

function hideSpinner() {
  document.getElementById('thinking-spinner').style.display = 'none';
}

function addLog(msg) {
  const log = document.getElementById('log');
  const line = document.createElement('div');
  line.textContent = msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function clearLog() {
  document.getElementById('log').innerHTML = '';
}

function openOutputFolder() {
  fetch('/api/open-output');
}

function toggleLogs() {
  const overlay = document.getElementById('logs-overlay');
  const open = overlay.style.display !== 'none';
  overlay.style.display = open ? 'none' : '';
  if (!open) {
    const log = document.getElementById('log');
    log.scrollTop = log.scrollHeight;
  }
}

function closeLogs() {
  document.getElementById('logs-overlay').style.display = 'none';
}

/* ── Инициализация ──────────────────────────────────────── */

function syncFileListHeight() {
  const drop = document.getElementById('drop-zone');
  const container = document.getElementById('file-list-container');
  if (drop && container) {
    container.style.height = drop.offsetHeight + 'px';
  }
}

async function init() {
  initTheme();
  syncFileListHeight();
  try {
    const gpuResp = await fetch('/api/gpu');
    const gpu = await gpuResp.json();
    const el = document.getElementById('gpu-status');
    el.textContent =
      `GPU: ${gpu.has_nvidia_gpu ? 'NVIDIA' : 'не обнаружена'} | Torch: ${gpu.cuda_available ? 'CUDA' : 'CPU'}`;
    el.classList.toggle('off', !gpu.cuda_available);
  } catch (_) {
    document.getElementById('gpu-status').textContent = 'GPU: ошибка проверки';
  }

  try {
    const llmResp = await fetch('/api/llm');
    const llm = await llmResp.json();
    const el = document.getElementById('llm-status');
    const engine = llm.engine ? ` (${llm.engine})` : '';
    if (llm.llm_ok) {
      el.textContent = `LLM${engine}: ${llm.model_ok ? 'модель найдена' : 'модель не найдена'}`;
    } else {
      el.textContent = `LLM${engine}: не обнаружен`;
      el.classList.add('off');
    }
  } catch (_) {
    document.getElementById('llm-status').textContent = 'LLM: ошибка';
  }
}

document.addEventListener('DOMContentLoaded', init);

window.addEventListener('resize', () => syncFileListHeight());

document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => e.preventDefault());

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const help = document.getElementById('help-overlay');
    const logs = document.getElementById('logs-overlay');
    if (help.style.display !== 'none') {
      closeHelp();
    } else if (logs.style.display !== 'none') {
      closeLogs();
    }
  }
});