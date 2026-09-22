/* PromptEar web UI — клиентская часть (v0.15) */

let files = [];
let taskId = null;
let eventSource = null;
let isDark = null;            // null = системная тема; true/false — переопределено юзером
let enhanceMode = 'auto';
let currentFileName = null;
let liveFile = null;
let launchParams = {};   // последние параметры запуска (формат, тайм-коды, ИИ, модель, GPU)
let appSettings = {whisper_model: 'base', use_gpu: false, output_format: 'docx', timestamps: false, ai_enabled: false};
let modelCatalog = {};
let installedModel = null;
let gemmaInstalled = false;
let gpuReport = null;
let activeDownload = null;   // {id, kind, model, label} | null
let downloadResolver = null; // {resolve, reject} — ожидающий startDownload()

function downloadBlockingRun() {
  return !!activeDownload && activeDownload.kind === 'whisper';
}
const fileStatuses = {};   // имя -> status
const fileTexts = {};      // имя -> последний текст (черновик/улучшенный)
const fileBadges = {};     // имя -> бейдж
const fileTranslations = {}; // имя -> {lang, text, outputPath} — последний перевод
let translateBusy = false;  // идёт перевод («Переводим…»)
let translateError = '';    // текст ошибки последнего перевода
let translateAbort = null;  // AbortController активного перевода
let translateCancelling = false; // пользователь нажал «Отмена»
let llmDownloadCb = null;   // колбэк после успешной установки модели ИИ
let llmPollTimer = null;
let llmCancelRequested = false; // пользователь нажал «Отмена» во время скачивания
const statusLabels = {
  queued: 'В очереди',
  processing: 'Подготовка',
  transcribing: 'В обработке',
  enhancing: 'Обработка',
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

/* ── Модальные окна ──────────────────────────────────────── */

function openModal(id) { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function openLaunchModal() {
  fillModal('launch-overlay');
  openModal('launch-overlay');
  document.getElementById('launch-apply').focus();
}
function closeLaunchModal() { closeModal('launch-overlay'); }

function openSettingsModal() {
  fillModal('settings-overlay');
  openModal('settings-overlay');
}
function closeSettingsModal() { closeModal('settings-overlay'); }

function modelOptions() {
  return Object.entries(modelCatalog)
    .sort((a, b) => a[1].size_mb - b[1].size_mb)
    .map(([alias, info]) => {
      const mark = info.installed ? ' ✓' : ` (~${info.size_mb} МБ)`;
      const desc = info.installed ? '' : ` — ${info.description}`;
      return `<option value="${alias}">${alias}${desc}${mark}</option>`;
    }).join('');
}

const FORMAT_HINTS = {
  docx: 'Документ Word (.docx) — для чтения и правок в Word.',
  txt: 'Простой текстовый файл (.txt).',
  md: 'Markdown-разметка (.md) — для Obsidian, GitHub, заметок.',
  srt: 'Субтитры SRT с тайм-кодами — для плееров.',
  vtt: 'Веб-субтитры VTT — для видео на сайтах и YouTube.',
};

function updateFormatHints() {
  for (const rootId of ['launch-overlay', 'settings-overlay']) {
    const el = document.querySelector(`#${rootId} [data-field="format"]`);
    const hint = document.getElementById(`${rootId.replace('-overlay', '')}-format-hint`);
    if (!el || !hint) continue;
    hint.textContent = FORMAT_HINTS[el.value] || '';
  }
}

function setGpuCheckbox(root) {
  const gpu = root.querySelector('[data-field="gpu"]');
  const label = root.querySelector('[data-field="gpu-label"]');
  if (!gpu) return;
  const noNvidia = !(gpuReport && gpuReport.has_nvidia_gpu);
  gpu.disabled = noNvidia;
  if (noNvidia) {
    gpu.checked = false;
    label.textContent = 'Использовать GPU (не обнаружен)';
  } else {
    gpu.checked = !!appSettings.use_gpu;
    label.textContent = 'Использовать GPU';
  }
}

function fillModal(rootId) {
  const root = document.getElementById(rootId);
  const field = (f) => root.querySelector(`[data-field="${f}"]`);
  const fmt = field('format');
  if (fmt) fmt.value = appSettings.output_format || 'docx';
  const ts = field('timestamps');
  if (ts) ts.checked = !!appSettings.timestamps;
  const ai = field('ai');
  if (ai) ai.checked = !!appSettings.ai_enabled;
  const model = field('model');
  if (model) {
    model.innerHTML = modelOptions();
    model.value = installedModel || appSettings.whisper_model || 'base';
  }
  setGpuCheckbox(root);
  updateFormatHints();
}

function collectModal(rootId) {
  const root = document.getElementById(rootId);
  const field = (f) => root.querySelector(`[data-field="${f}"]`);
  return {
    output_format: field('format').value,
    timestamps: !!field('timestamps').checked,
    ai_enabled: !!field('ai').checked,
    whisper_model: field('model').value,
    use_gpu: !!field('gpu').checked,
  };
}

async function saveSettings(patch) {
  const resp = await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(patch),
  });
  const data = await resp.json();
  Object.assign(appSettings, data);
  return data;
}

async function applyAndRun() {
  if (downloadBlockingRun()) {
    addLog('⚠ Идёт скачивание модели — дождитесь завершения');
    return;
  }
  const params = collectModal('launch-overlay');
  launchParams = params;
  appSettings = Object.assign({}, appSettings, params);
  try {
    await saveSettings(params);
  } catch (err) {
    addLog(`Ошибка сохранения настроек: ${err.message}`);
    return;
  }
  closeLaunchModal();
  runPipeline();
}

async function saveSettingsFromModal() {
  const params = collectModal('settings-overlay');
  launchParams = params;
  appSettings = Object.assign({}, appSettings, params);
  try {
    await saveSettings(params);
  } catch (err) {
    addLog(`Ошибка сохранения настроек: ${err.message}`);
    return;
  }
  const portInput = document.querySelector('#settings-overlay [data-field="llm-port"]');
  if (portInput && portInput.value) {
    await applyLlmPort(parseInt(portInput.value, 10));
  }
  closeSettingsModal();
  addLog('✅ Настройки сохранены');
}

/* ── Подтверждение и скачивания ──────────────────────────── */

function confirmPopup(message) {
  return new Promise((resolve) => {
    document.getElementById('confirm-text').textContent = message;
    openModal('confirm-overlay');
    const yes = document.getElementById('confirm-yes');
    const no = document.getElementById('confirm-no');
    const cleanup = () => {
      yes.onclick = null;
      no.onclick = null;
      closeModal('confirm-overlay');
    };
    yes.onclick = () => { cleanup(); resolve(true); };
    no.onclick = () => { cleanup(); resolve(false); };
  });
}

function showDownloadModal() {
  openModal('download-overlay');
  const bar = document.getElementById('dl-bar');
  const pct = document.getElementById('dl-pct');
  bar.classList.remove('indeterminate');
  bar.style.width = '0%';
  pct.textContent = '';
}
function hideDownloadModal() { closeModal('download-overlay'); }

function updateDownloadUi(msg) {
  const label = document.getElementById('dl-label');
  const bar = document.getElementById('dl-bar');
  const pct = document.getElementById('dl-pct');
  label.textContent = msg.label || 'Скачивание…';
  if (msg.total_mb != null && msg.total_mb > 0) {
    bar.classList.remove('indeterminate');
    const p = Math.max(0, Math.min(100, msg.pct));
    bar.style.width = p + '%';
    pct.textContent = `${msg.done_mb.toFixed(1)} / ${msg.total_mb.toFixed(1)} МБ (${p}%)`;
  } else {
    bar.classList.add('indeterminate');
    pct.textContent = '';
  }
}

function startDownload(kind, model) {
  return new Promise((resolve, reject) => {
    if (downloadResolver) {
      reject(new Error('Скачивание уже идёт'));
      return;
    }
    downloadResolver = {resolve, reject};
    showDownloadModal();
    fetch('/api/downloads', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kind, model}),
    })
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .then(({ok, d}) => {
        if (!ok) {
          throw new Error(d.error || 'Скачивание не запустилось');
        }
      })
      .catch((err) => {
        downloadResolver = null;
        hideDownloadModal();
        addLog(`Ошибка: ${err.message}`);
        reject(err);
      });
  });
}

function handleDownloadEvent(msg) {
  switch (msg.type) {
    case 'download_active':
      activeDownload = msg.status && msg.status.active;
      if (activeDownload) showDownloadModal();
      break;
    case 'download_progress':
      activeDownload = activeDownload || {};
      activeDownload.label = msg.label;
      updateDownloadUi(msg);
      break;
    case 'download_done': {
      activeDownload = null;
      const r = downloadResolver;
      downloadResolver = null;
      hideDownloadModal();
      if (r) r.resolve();
      refreshModels();
      refreshGpu();
      refreshLlmStatus();
      break;
    }
    case 'download_failed': {
      const err = new Error(msg.error || 'Скачивание не удалось');
      activeDownload = null;
      const r = downloadResolver;
      downloadResolver = null;
      hideDownloadModal();
      addLog('❌ ' + err.message);
      if (r) r.reject(err);
      break;
    }
  }
}

function connectDownloads() {
  const es = new EventSource('/api/downloads/stream');
  es.onmessage = (e) => {
    if (e.data === ': keepalive') return;
    try {
      handleDownloadEvent(JSON.parse(e.data));
    } catch (_) {}
  };
}

async function refreshGpu() {
  try {
    gpuReport = await (await fetch('/api/gpu')).json();
    refreshGpuStatusUI();
  } catch (_) {
    const el = document.getElementById('gpu-status');
    el.classList.remove('off');
    el.classList.add('red');
    el.textContent = 'GPU: ошибка проверки';
  }
}

function refreshGpuStatusUI() {
  const el = document.getElementById('gpu-status');
  if (!gpuReport) { el.textContent = 'GPU: …'; return; }
  const onGpu = !!appSettings.use_gpu && !!gpuReport.cuda_available;
  el.textContent = onGpu ? 'Используется GPU' : 'Используется CPU';
  el.classList.toggle('off', !onGpu);
  el.classList.remove('red');
}

async function refreshModels() {
  try {
    const data = await (await fetch('/api/models')).json();
    modelCatalog = data.catalog || {};
    installedModel = data.installed;
    gemmaInstalled = !!data.gemma_installed;
    if (data.current) appSettings.whisper_model = data.current;
    syncEnhanceButton();
  } catch (_) {}
}

async function refreshSettings() {
  try {
    const data = await (await fetch('/api/settings')).json();
    appSettings = Object.assign({}, appSettings, data);
    const portInput = document.querySelector('#settings-overlay [data-field="llm-port"]');
    if (portInput && data.llm_port) portInput.value = data.llm_port;
    refreshGpuStatusUI();
  } catch (_) {}
}

/* ── Поля настройки: изменение модели / ИИ / GPU ──────────── */

function handleModelChange(e) {
  const sel = e.target;
  const target = sel.value;
  const info = modelCatalog[target];
  if (!info) return;
  if (info.installed || target === installedModel) return;
  const cur = installedModel ? ` Текущая модель (${installedModel}) будет удалена.` : '';
  confirmPopup(`Скачать модель ${target} (~${info.size_mb} МБ)?${cur}`)
    .then(async (ok) => {
      if (!ok) {
        sel.value = installedModel || appSettings.whisper_model || 'base';
        return;
      }
      try {
        await startDownload('whisper', target);
        await refreshModels();
      } catch (_) {
        sel.value = installedModel || appSettings.whisper_model || 'base';
      }
    });
}

function handleAiChange(e) {
  const cb = e.target;
  if (!cb.checked) return;
  if (gemmaInstalled) return;
  confirmPopup('Скачать модель ИИ (~3 ГБ)? Это займёт время. Продолжить?')
    .then(async (ok) => {
      if (!ok) {
        cb.checked = false;
        return;
      }
      try {
        await startDownload('gemma');
        await refreshModels();
      } catch (_) {
        cb.checked = false;
      }
    });
}

async function handleGpuChange(e) {
  const cb = e.target;
  if (!gpuReport) return;
  if (!cb.checked) {
    appSettings.use_gpu = false;
    try {
      await saveSettings({use_gpu: false});
    } catch (_) {}
    refreshGpuStatusUI();
    const restart = await confirmPopup('GPU выключен. Перезапустить программу?');
    if (restart) {
      fetch('/api/restart', {method: 'POST'}).catch(() => {});
    }
    return;
  }
  confirmPopup('Проверить доступность GPU?')
    .then(async (ok) => {
      if (!ok) {
        cb.checked = false;
        return;
      }
      if (!gpuReport.has_nvidia_gpu) {
        await confirmPopup('GPU NVIDIA не обнаружен. Доступность не подтверждена.');
        cb.checked = false;
        return;
      }
      const enable = await confirmPopup('Доступен. Включить использование видеокарты?');
      if (!enable) {
        cb.checked = false;
        return;
      }
      try {
        await saveSettings({use_gpu: true});
        appSettings.use_gpu = true;
        refreshGpuStatusUI();
        const restart = await confirmPopup('GPU включён. Перезапустить программу?');
        if (restart) {
          fetch('/api/restart', {method: 'POST'}).catch(() => {});
        }
      } catch (_) {
        cb.checked = false;
      }
    })
    .catch(() => {
      cb.checked = false;
    });
}

document.addEventListener('change', (e) => {
  const el = e.target;
  const field = el && el.dataset && el.dataset.field;
  if (field === 'model') handleModelChange(e);
  else if (field === 'ai') handleAiChange(e);
  else if (field === 'gpu') handleGpuChange(e);
  else if (field === 'format') updateFormatHints();
});

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
  const name = files[idx].name;
  const st = fileStatuses[name];
  // Файл в работе или в очереди запущенной задачи: остановить обработку на бэке.
  // У бэка свой список файлов, просто убрать строку из UI недостаточно.
  if (taskId && st && st !== 'done' && st !== 'skipped') {
    fetch(`/api/skip/${taskId}/${encodeURIComponent(name)}`, { method: 'POST' }).catch(() => {});
  }
  fileStatuses[name] = undefined;
  delete fileTexts[name];
  delete fileBadges[name];
  delete fileTranslations[name];
  files.splice(idx, 1);
  if (currentFileName === name) currentFileName = null;
  if (liveFile === name) liveFile = null;
  renderFileList();
  // перевести live-индикацию на оставшийся активный файл
  const next = firstActiveFile();
  if (next) {
    setLiveFile(next);
    document.getElementById('skip-btn').style.display = '';
  } else {
    document.getElementById('skip-btn').style.display = 'none';
  }
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
    const selected = f.name === currentFileName && clickable;
    return `<li class="file-item ${clickable ? 'clickable' : ''} ${selected ? 'selected' : ''}"
      data-name="${escapeHtml(f.name)}" onclick="selectFile('${escapeJs(f.name)}')">
      <span class="file-num">${i + 1}.</span>
      <span class="file-name">${escapeHtml(f.name)}</span>
      <span class="file-size">${formatSize(f.size)}</span>
      <span class="status status-${st}">${statusLabels[st] || 'В очереди'}</span>
      <span class="remove" onclick="event.stopPropagation(); removeFile(${i})" title="Убрать из списка" aria-label="Убрать из списка">✕</span>
    </li>`;
  }).join('');
}

function selectFile(name) {
  if (!fileStatuses[name] || fileStatuses[name] === 'queued') return;
  currentFileName = name;
  renderFileList();
  const text = fileTexts[name] || '';
  const improved = fileBadges[name] === 'Улучшено ИИ';
  const tr = fileTranslations[name];
  if (tr) {
    // файл был переведён — показываем последний перевод
    setResultText(tr.text, true);
  } else {
    setResultText(text, improved);
  }
  syncEnhanceButton();
  syncTranslateButton();
}

// Кнопка «Перевести» — видна всегда, когда есть текст результата;
// в ручном режиме (none) её не прячем — перевод независим от улучшения.
// Состояния: idle «Перевести» → busy «Отмена»+спиннер → «Переведено (LANG)».
// Кнопка «Оригинал» (↺) — видна только когда у файла есть сохранённый перевод.
function onTranslateClick() {
  if (translateBusy) {
    cancelTranslate();
    return;
  }
  openTranslateModal();
}

function syncTranslateButton() {
  const name = currentFileName;
  const text = (name && fileTexts[name]) || '';
  const btn = document.getElementById('translate-btn');
  const label = document.getElementById('translate-label');
  const iconEl = document.getElementById('translate-icon');
  const spinner = document.getElementById('translate-spinner');
  const sel = document.getElementById('translate-lang');
  const resetBtn = document.getElementById('translate-reset-btn');
  const hasTr = !!(name && fileTranslations[name]);
  if (resetBtn) resetBtn.style.display = hasTr ? '' : 'none';
  if (enhanceBusy) {
    // идёт улучшение — кнопка перевода не нужна (показан «Остановить улучшение»)
    btn.style.display = 'none';
    if (sel) sel.style.display = 'none';
    if (resetBtn) resetBtn.style.display = 'none';
    return;
  }
  if (translateBusy) {
    btn.style.display = '';
    btn.disabled = false; // это теперь «Остановить перевод»
    btn.classList.add('busy');
    if (label) label.textContent = 'Остановить перевод';
    if (spinner) spinner.style.display = '';
    if (iconEl) iconEl.style.display = 'none';
    if (sel) sel.style.display = 'none';
    if (resetBtn) resetBtn.style.display = 'none';
    return;
  }
  btn.disabled = false;
  btn.classList.remove('busy');
  if (spinner) spinner.style.display = 'none';
  if (iconEl) iconEl.style.display = '';
  if (label) {
    const tr = name && fileTranslations[name];
    label.textContent = (tr && tr.lang)
      ? `Переведено (${tr.lang.toUpperCase()})`
      : 'Перевести';
  }
  if (text) {
    btn.style.display = '';
    if (sel) sel.style.display = '';
  } else {
    btn.style.display = 'none';
    if (sel) sel.style.display = 'none';
  }
}

// Возвращает исходный текст в лайв-окно (перевод остаётся сохранён в файле,
// удаляется только из состояния просмотра, чтобы можно было переводить заново).
function resetTranslation() {
  const name = currentFileName;
  if (!name || !fileTranslations[name]) return;
  delete fileTranslations[name];
  const improved = fileBadges[name] === 'Улучшено ИИ';
  setResultText(fileTexts[name] || '', improved);
  syncEnhanceButton();
  syncTranslateButton();
}

// Статус/кнопка «Улучшить с ИИ» для текущего файла:
//  - переведённый текст → кнопка «Улучшить с ИИ» (улучшается исходная расшифровка)
//  - улучшенный текст → не-кликабельный статус «Улучшено с ИИ»
//  - черновик в ручном режиме → кнопка «Улучшить с ИИ»
//  - в режиме «с обработкой» (auto), когда нет перевода → кнопки нет (SSE 'result')
// Единое место синхронизации: вызывается при выборе файла и после улучшения.
function syncEnhanceButton() {
  const btn = document.getElementById('enhance-btn');
  const label = document.getElementById('enhance-label');
  const iconEl = document.getElementById('enhance-icon');
  const spinner = document.getElementById('enhance-spinner');
  if (enhanceBusy) {
    // идёт улучшение: вместо статуса — кнопка «Остановить улучшение» со спиннером
    btn.style.display = '';
    btn.disabled = false;
    btn.classList.add('busy');
    if (label) label.textContent = 'Остановить улучшение';
    if (iconEl) iconEl.style.display = 'none';
    if (spinner) spinner.style.display = '';
    hideEnhanceStatus();
    return;
  }
  if (translateBusy) {
    // идёт перевод — кнопка улучшения не нужна (показан «Остановить перевод»)
    hideEnhanceButton();
    return;
  }
  btn.disabled = false;
  btn.classList.remove('busy');
  if (spinner) spinner.style.display = 'none';
  if (iconEl) iconEl.style.display = '';
  if (label) label.textContent = 'Улучшить с ИИ';
  const name = currentFileName;
  const text = (name && fileTexts[name]) || '';
  const improved = fileBadges[name] === 'Улучшено ИИ';
  const tr = fileTranslations[name];
  if (tr) {
    // после перевода кнопку «Улучшить с ИИ» не прячем: улучшение работает по
    // исходной расшифровке (перевод — отдельный файл и остаётся на диске),
    // после улучшения текст в лайв-окне снова на исходном языке, перевести можно повторно
    hideEnhanceStatus();
    showEnhanceButton();
  } else if (improved) {
    hideEnhanceButton();
    showEnhanceStatus('Улучшено с ИИ');
  } else if (enhanceMode !== 'auto' && text) {
    showEnhanceButton();
  } else {
    hideEnhanceButton();
    hideEnhanceStatus();
  }
}

/* ── Загрузка и обработка ────────────────────────────────── */

async function runPipeline() {
  if (files.length === 0) return;
  if (downloadBlockingRun()) {
    addLog('⚠ Идёт скачивание модели — дождитесь завершения');
    return;
  }

  const p = launchParams && launchParams.whisper_model ? launchParams : appSettings;
  const formData = new FormData();
  for (const f of files) {
    formData.append('files', f);
  }
  formData.append('ai', p.ai_enabled ? '1' : '0');
  enhanceMode = p.ai_enabled ? 'auto' : 'none';
  formData.append('output_format', p.output_format || 'docx');
  formData.append('timestamps', p.timestamps ? '1' : '0');
  formData.append('whisper_model', p.whisper_model || 'base');
  formData.append('use_gpu', p.use_gpu ? '1' : '0');

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
      }
      if (msg.final && enhanceMode !== 'auto') {
        syncEnhanceButton();
      }
      if (msg.final) {
        syncTranslateButton();
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

      } else if (lastPassText != null) {
        // предыдущий проход завершён — его полный текст мы уже получили,
        // плавно переходим: мигание → затухание → появление
        startPassTransition(lastPassText);
      }
      break;

    case 'enhancing_stream':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Обработка';
      // не печатаем по токенам: копим полный текст прохода,
      // он появится целиком на переходе к следующему проходу
      lastPassText = msg.text;
      break;

    case 'result':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      liveFile = currentFileName || msg.filename || firstActiveFile();
      setLiveFile(liveFile);
      fileTexts[liveFile] = msg.text;
      fileBadges[liveFile] = 'Улучшено ИИ';
      hideEnhanceProgress();
      startPassTransition(msg.text);
      syncEnhanceButton();
      syncTranslateButton();
      break;

    case 'file_status':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileStatuses[msg.filename] = msg.status;
      if (msg.status === 'transcribing') {
        liveFile = msg.filename;
        setLiveFile(liveFile);
        document.getElementById('skip-btn').style.display = '';
      }
      renderFileList();
      break;

    case 'skipped':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileStatuses[msg.filename] = 'skipped';
      fileBadges[msg.filename] = fileBadges[msg.filename] || 'Черновик';
      liveFile = currentFileName || msg.filename;
      setLiveFile(liveFile);
      document.getElementById('skip-btn').style.display = 'none';
      renderFileList();
      break;

    case 'progress':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
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
      if (enhanceMode !== 'auto') {
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
  if (enhanceMode === 'auto') {
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
  hideEnhanceStatus();
  hideEnhanceProgress();
  setResultText('', false);
  document.getElementById('translate-btn').style.display = 'none';
  document.getElementById('translate-lang').style.display = 'none';
  const resetBtn = document.getElementById('translate-reset-btn');
  if (resetBtn) resetBtn.style.display = 'none';

}



function setResultText(text, improved) {
  resetTyping();
  const el = document.getElementById('result-text');
  el.textContent = text;

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

let enhanceBusy = false;
let enhanceAbort = null;       // AbortController активного улучшения
let enhanceCancelling = false; // пользователь нажал «Остановить улучшение»

function onEnhanceClick() {
  if (enhanceBusy) {
    cancelEnhance();
  } else {
    enhanceDraft();
  }
}

// Прерывает идущее улучшение: рвёт HTTP-запрос и сигналит серверу (llama стопает генерацию).
async function cancelEnhance() {
  if (!enhanceBusy || !taskId || !currentFileName) return;
  enhanceCancelling = true;
  if (enhanceAbort) enhanceAbort.abort();
  try {
    await fetch(
      `/api/enhance/cancel/${taskId}/${encodeURIComponent(currentFileName)}`,
      { method: 'POST' }
    );
  } catch (e) { /* не критично — HTTP уже прерван */ }
}

function showEnhanceStatus(text) {
  const st = document.getElementById('enhance-status');
  st.textContent = text;
  st.style.display = '';
  document.getElementById('enhance-btn').style.display = 'none';
}

function hideEnhanceStatus() {
  document.getElementById('enhance-status').style.display = 'none';
}

function showEnhanceButton() {
  document.getElementById('enhance-btn').style.display = '';
  hideEnhanceStatus();
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
  if (!taskId || !currentFileName || enhanceBusy) return;
  // задача уже завершена и SSE закрыт (finish) — переподключаемся,
  // чтобы лайв-трансляция показывала проходы улучшения
  if (!eventSource) {
    connectSSE(taskId);
  }
  const prevTr = fileTranslations[currentFileName]; // был перевод — обновим после улучшения
  const retranslate = !!(prevTr && prevTr.lang);
  enhanceCancelling = false;
  enhanceBusy = true;
  enhanceAbort = new AbortController();
  hideEnhanceStatus();           // статус «Улучшаем…» заменяет кнопка «Остановить улучшение»
  showEnhanceProgress(1, 3);
  fileStatuses[currentFileName] = 'enhancing';
  fileBadges[currentFileName] = 'Обработка';
  syncEnhanceButton();
  syncTranslateButton(); // во время улучшения кнопку «Перевести» скрываем
  renderFileList();
  let enhanceOk = false;
  try {
    const resp = await fetch(`/api/enhance/${taskId}/${encodeURIComponent(currentFileName)}`, {
      method: 'POST',
      signal: enhanceAbort.signal,
    });
    const data = await resp.json();
    if (data.error) {
      addLog(`❌ ${data.error}`);
    } else {
      enhanceOk = true;
      fileTexts[currentFileName] = data.text;
      fileBadges[currentFileName] = 'Улучшено ИИ';
      hideEnhanceProgress();
      setResultText(data.text, true);
      addLog('✅ Текст улучшен и перезаписан');
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      addLog('⏹ Улучшение остановлено');
    } else {
      addLog(`❌ ${err.message}`);
    }
  } finally {
    enhanceBusy = false;
    enhanceAbort = null;
    enhanceCancelling = false;
    hideEnhanceProgress();
    fileStatuses[currentFileName] = 'done';
    renderFileList();
    if (enhanceOk) {
      if (retranslate) {
        // был перевод — автоматически переводим улучшенный текст на тот же язык
        // (сервер уже хранит улучшенный текст в result.text — отдельный вызов translate)
        syncEnhanceButton();
        syncTranslateButton();
      } else {
        // успешно улучшенный файл: показываем не-кликабельный статус
        showEnhanceStatus('Улучшено с ИИ');
        syncTranslateButton();
      }
    } else {
      // ошибка/отмена улучшения — вернуть кнопку, можно повторить
      syncEnhanceButton();
      syncTranslateButton();
    }
  }
  if (enhanceOk && retranslate) {
    await doTranslate(prevTr.lang, prevTr.langName || prevTr.lang);
  }
}

function openTranslateModal() {
  if (!taskId || !currentFileName || translateBusy) return;
  document.getElementById('translate-overlay').style.display = '';
}

function closeTranslateModal() {
  document.getElementById('translate-overlay').style.display = 'none';
}

// Предложение скачать модель ИИ (gemma), если она нужна для перевода.
function offerLlmDownload(cb) {
  llmDownloadCb = cb || null;
  document.getElementById('llm-dl-progress').style.display = 'none';
  document.getElementById('llm-dl-text').style.display = '';
  document.getElementById('llm-dl-error').style.display = 'none';
  document.getElementById('llm-dl-error').textContent = '';
  document.getElementById('llm-dl-yes').disabled = false;
  document.getElementById('llm-dl-yes').textContent = 'Скачать модель';
  document.getElementById('llm-dl-yes').style.display = '';
  document.getElementById('llm-dl-cancel').disabled = false;
  document.getElementById('llm-dl-cancel').style.display = '';
  document.getElementById('llm-dl-overlay').style.display = '';
}

function closeLlmDownload() {
  if (llmPollTimer) { clearTimeout(llmPollTimer); llmPollTimer = null; }
  document.getElementById('llm-dl-overlay').style.display = 'none';
  llmDownloadCb = null;
}

async function startLlmDownload() {
  llmCancelRequested = false;
  document.getElementById('llm-dl-yes').style.display = 'none';
  document.getElementById('llm-dl-cancel').style.display = '';
  document.getElementById('llm-dl-cancel').textContent = 'Отмена';
  document.getElementById('llm-dl-cancel').disabled = false;
  document.getElementById('llm-dl-text').style.display = 'none';
  document.getElementById('llm-dl-error').style.display = 'none';
  const prog = document.getElementById('llm-dl-progress');
  prog.style.display = '';
  document.getElementById('llm-dl-bar').style.width = '40%';
  document.getElementById('llm-dl-bar').classList.add('indeterminate');
  document.getElementById('llm-dl-label').textContent = 'Подготовка…';
  try {
    await fetch('/api/llm/download', { method: 'POST' });
  } catch (e) { /* опрос всё равно покажет состояние */ }
  pollLlmInstall();
}

// Прерывает скачивание модели ИИ (активно только во время скачивания).
async function cancelLlmDownload() {
  llmCancelRequested = true;
  if (llmPollTimer) { clearTimeout(llmPollTimer); llmPollTimer = null; }
  document.getElementById('llm-dl-cancel').disabled = true;
  document.getElementById('llm-dl-label').textContent = 'Остановка…';
  try {
    await fetch('/api/llm/cancel', { method: 'POST' });
  } catch (e) { /* сервер всё равно увидит cancel при опросе */ }
  closeLlmDownload();
}

function pollLlmInstall() {
  clearTimeout(llmPollTimer);
  llmPollTimer = setTimeout(async () => {
    try {
      const resp = await fetch('/api/llm');
      const st = await resp.json();
      if (st.installing) {
        document.getElementById('llm-dl-label').textContent = 'Скачивание модели ИИ… (может занять несколько минут)';
        pollLlmInstall();
        return;
      }
      if (llmCancelRequested) {
        document.getElementById('llm-dl-overlay').style.display = 'none';
        llmCancelRequested = false;
        llmDownloadCb = null;
        return;
      }
      if (st.llm_ok && st.model_ok) {
        document.getElementById('llm-dl-overlay').style.display = 'none';
        const cb = llmDownloadCb;
        llmDownloadCb = null;
        if (cb) cb();
        return;
      }
      const msg = st.install_error || st.error || 'Не удалось установить модель';
      document.getElementById('llm-dl-label').textContent = 'Не удалось установить модель';
      const errEl = document.getElementById('llm-dl-error');
      errEl.textContent = msg;
      errEl.style.display = '';
      document.getElementById('llm-dl-progress').style.display = 'none';
      document.getElementById('llm-dl-yes').style.display = '';
      document.getElementById('llm-dl-yes').textContent = 'Повторить';
      document.getElementById('llm-dl-cancel').style.display = '';
    } catch (err) {
      document.getElementById('llm-dl-label').textContent = 'Ошибка связи с сервером';
      pollLlmInstall();
    }
  }, 2500);
}

async function translateDraft() {
  if (!taskId || !currentFileName || translateBusy) return;
  closeTranslateModal();
  const sel = document.getElementById('translate-lang');
  const lang = sel ? sel.value : 'en';
  const langName = sel && sel.selectedOptions && sel.selectedOptions[0]
    ? sel.selectedOptions[0].textContent
    : lang;
  await doTranslate(lang, langName);
}

// Переводит текущий файл на язык (lang/langName) и показывает результат.
// Возвращает true при успехе. Используется и по кнопке перевода, и для
// автоматического перевода улучшенного текста после «Улучшить с ИИ».
async function doTranslate(lang, langName) {
  if (!taskId || !currentFileName || translateBusy) return false;
  translateError = '';
  translateCancelling = false;
  translateBusy = true;
  translateAbort = new AbortController();
  fileStatuses[currentFileName] = 'enhancing'; // «Обработка» в списке файлов
  hideEnhanceStatus();
  syncTranslateButton();
  syncEnhanceButton(); // во время перевода кнопку «Улучшить» скрываем
  renderFileList();
  let ok = false;
  try {
    const resp = await fetch(
      `/api/translate/${taskId}/${encodeURIComponent(currentFileName)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: translateAbort.signal,
        body: JSON.stringify({ language_code: lang, language_name: langName }),
      }
    );
    const data = await resp.json();
    if (data.error) {
      addLog(`❌ ${data.error}`);
      throw new Error(data.error);
    } else {
      ok = true;
      const name = currentFileName;
      fileTranslations[name] = {
        lang,
        langName: data.language_name || langName || lang,
        text: data.text,
        outputPath: data.output_path,
      };
      fileStatuses[name] = 'done';
      setResultText(data.text, true);
      addLog(`✅ Перевод (${langName}): ${data.output_path}`);
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      translateError = '';
      addLog('⏹ Перевод отменён');
    } else {
      translateError = err.message;
      addLog(`❌ ${err.message}`);
    }
  } finally {
    translateBusy = false;
    translateAbort = null;
    translateCancelling = false;
    fileStatuses[currentFileName] = 'done';
    renderFileList();
    if (ok) {
      translateError = '';
      syncEnhanceButton();
      syncTranslateButton();
    } else if (translateError) {
      // ошибка перевода — вернуть кнопку, можно повторить; показать причину
      syncEnhanceButton();
      syncTranslateButton();
      if (/LLM|модель|gemma|ИИ/i.test(translateError)) {
        offerLlmDownload(() => doTranslate(lang, langName));
      } else {
        showEnhanceStatus(`⚠ Перевод не выполнен (${translateError})`);
      }
    } else {
      // отменено пользователем — просто вернуть кнопку
      syncEnhanceButton();
      syncTranslateButton();
    }
  }
  return ok;
}

// Прерывает идущий перевод: рвёт HTTP-запрос и сигналит серверу (llama стопает генерацию).
async function cancelTranslate() {
  if (!translateBusy || !taskId || !currentFileName) return;
  translateCancelling = true;
  if (translateAbort) translateAbort.abort();
  try {
    await fetch(
      `/api/translate/cancel/${taskId}/${encodeURIComponent(currentFileName)}`,
      { method: 'POST' }
    );
  } catch (e) { /* не критично — HTTP уже прерван */ }
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
        syncEnhanceButton();
        syncTranslateButton();
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
  connectDownloads();
  await refreshGpu();
  await refreshModels();
  await refreshSettings();
  refreshLlmStatus();
  try {
    const st = await (await fetch('/api/downloads/status')).json();
    if (st.active) {
      activeDownload = st.active;
      showDownloadModal();
    }
  } catch (_) {}
}

async function refreshLlmStatus() {
  try {
    const llmResp = await fetch('/api/llm');
    const llm = await llmResp.json();
    const el = document.getElementById('llm-status');
    const engine = llm.engine ? ` (${llm.engine})` : '';
    const errBox = document.getElementById('llm-error-box');
    const portField = document.querySelector(
      '#settings-overlay .modal-port-row'
    )?.closest('.modal-field');
    if (llm.llm_ok) {
      el.classList.remove('hidden');
      if (portField) portField.classList.remove('hidden');
      el.textContent = `LLM${engine}: ${llm.model_ok ? 'модель найдена' : 'модель не найдена'}`;
      el.classList.remove('off');
      if (errBox) errBox.classList.add('hidden');
    } else if (!llm.model_ok) {
      el.classList.add('hidden');
      if (portField) portField.classList.add('hidden');
      if (errBox) errBox.classList.add('hidden');
    } else {
      el.classList.remove('hidden');
      if (portField) portField.classList.remove('hidden');
      el.textContent = `LLM${engine}: не обнаружен`;
      el.classList.add('off');
      if (errBox) {
        document.getElementById('llm-error-text').textContent =
          llm.error || 'Порт по умолчанию занят или движок не запущен.';
        errBox.classList.remove('hidden');
      }
    }
  } catch (_) {
    document.getElementById('llm-status').textContent = 'LLM: ошибка';
  }
}

async function applyLlmPort(portArg) {
  const input = document.getElementById('llm-port-input');
  const btn = document.getElementById('llm-port-apply');
  const port = portArg || (input ? parseInt(input.value, 10) : NaN);
  if (!port || isNaN(port)) {
    if (input) {
      input.value = '';
      input.placeholder = 'Введите порт 1024–65535';
    }
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch('/api/llm/port', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({port}),
    });
    const data = await resp.json();
    if (btn) btn.disabled = false;
    if (data.ok) {
      document.getElementById('llm-error-box').classList.add('hidden');
      document.getElementById('llm-status').textContent =
        `LLM (${data.engine}): запущен на порту ${data.port}`;
      document.getElementById('llm-status').classList.remove('off');
      return {ok: true, port: data.port};
    }
    document.getElementById('llm-error-text').textContent =
      data.error || 'Не удалось запустить LLM на этом порту.';
    return {ok: false, error: data.error};
  } catch (_) {
    if (btn) btn.disabled = false;
    document.getElementById('llm-error-text').textContent =
      'Ошибка соединения с сервером.';
    return {ok: false, error: 'Ошибка соединения с сервером.'};
  }
}

document.addEventListener('DOMContentLoaded', init);

document.getElementById('llm-port-apply')?.addEventListener('click', applyLlmPort);

window.addEventListener('resize', () => syncFileListHeight());

document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => e.preventDefault());

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    for (const id of ['launch-overlay', 'settings-overlay', 'confirm-overlay']) {
      const m = document.getElementById(id);
      if (m && m.style.display !== 'none') {
        closeModal(id);
        return;
      }
    }
    const help = document.getElementById('help-overlay');
    const logs = document.getElementById('logs-overlay');
    if (help.style.display !== 'none') {
      closeHelp();
    } else if (logs.style.display !== 'none') {
      closeLogs();
    }
  }
});