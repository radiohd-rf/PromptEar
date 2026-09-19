/* PromptEar web UI — клиентская часть (v0.15) */

let files = [];
let taskId = null;
let eventSource = null;
let isDark = true;
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

function toggleTheme() {
  isDark = !isDark;
  document.body.classList.toggle('light', !isDark);
  document.getElementById('theme-toggle').textContent = isDark ? '☀️' : '🌙';
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
  const count = document.getElementById('file-count');

  if (files.length === 0) {
    document.getElementById('drop-text').textContent = 'Перетащите аудиофайлы сюда';
    list.innerHTML = '';
  } else {
    document.getElementById('drop-text').textContent = 'Перетащите ещё файлы';
  }

  count.textContent = `Выбрано: ${files.length} файлов`;

  list.innerHTML = files.map((f, i) => {
    const st = fileStatuses[f.name] || 'queued';
    const clickable = st !== 'queued';
    const removable = !fileStatuses[f.name] || st === 'queued';
    const selected = f.name === currentFileName && clickable;
    return `<li class="file-item ${clickable ? 'clickable' : ''} ${selected ? 'selected' : ''}"
      data-name="${escapeHtml(f.name)}" onclick="selectFile('${escapeJs(f.name)}')">
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
      setResultText(msg.text, false);
      if (msg.final && enhanceMode === 'ask') {
        showEnhanceButton();
      }
      break;

    case 'enhancing':
      showEnhanceProgress(msg.active_pass, msg.total_passes);
      break;

    case 'enhancing_stream':
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Улучшение ИИ';
      setResultText(msg.text, false);
      break;

    case 'result':
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Улучшено ИИ';
      hideEnhanceProgress();
      setResultText(msg.text, true);
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
  const el = document.getElementById('result-text');
  el.textContent = text;
  updateBadge(improved ? 'Улучшено ИИ' : (text ? 'Черновик (Whisper)' : 'Черновик'));
}

function showEnhanceProgress(activePass, totalPasses) {
  const wrap = document.getElementById('enhance-progress');
  wrap.style.display = 'flex';
  const bar = document.getElementById('enhance-progress-bar');
  bar.style.width = `${Math.round((activePass / totalPasses) * 100)}%`;
  document.getElementById('enhance-progress-label').textContent =
    `Проход ${activePass}/${totalPasses}`;
}

function hideEnhanceProgress() {
  document.getElementById('enhance-progress').style.display = 'none';
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
  btn.textContent = 'Улучшаем…';
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
    btn.textContent = '✨ Улучшить с ИИ';
    hideEnhanceButton();
  }
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
  document.getElementById('run-btn').textContent = '▶ Запуск';
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

/* ── Инициализация ──────────────────────────────────────── */

async function init() {
  try {
    const gpuResp = await fetch('/api/gpu');
    const gpu = await gpuResp.json();
    document.getElementById('gpu-status').textContent =
      `GPU: ${gpu.has_nvidia_gpu ? 'NVIDIA' : 'не обнаружена'} | Torch: ${gpu.cuda_available ? 'CUDA' : 'CPU'}`;
    if (gpu.cuda_available) document.getElementById('gpu-status').style.color = 'var(--success)';
  } catch (_) {
    document.getElementById('gpu-status').textContent = 'GPU: ошибка проверки';
  }

  try {
    const llmResp = await fetch('/api/llm');
    const llm = await llmResp.json();
    const el = document.getElementById('llm-status');
    const engine = llm.engine ? ` (${llm.engine})` : '';
    if (llm.llm_ok) {
      el.textContent = `LLM${engine}: ${llm.model_ok ? '✅' : '⚠ модель не найдена'}`;
    } else {
      el.textContent = `LLM${engine}: не обнаружен`;
    }
  } catch (_) {
    document.getElementById('llm-status').textContent = 'LLM: ошибка';
  }
}

document.addEventListener('DOMContentLoaded', init);

document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => e.preventDefault());

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const overlay = document.getElementById('help-overlay');
    if (overlay.style.display !== 'none') {
      closeHelp();
    }
  }
});