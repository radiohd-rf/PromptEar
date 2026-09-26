/* PromptEar web UI — клиентская часть (v0.15) */

let files = [];
let taskId = null;
let eventSource = null;
let isDark = null;            // null = системная тема; true/false — переопределено юзером
let enhanceMode = 'auto';
let currentFileName = null;
let liveFile = null;
// Файл, явно выбранный кликом (selectFile). Пока закреплён — окно не
// уводят события других файлов (только сохраняем их тексты/статусы).
// Снимается новым запуском. Без закрепления окно следует за работой.
let userPinnedFile = null;
let expectedDones = 1;      // сколько done-событий ждать от сервера (2 при переводе)
let launchParams = {};   // последние параметры запуска (формат, тайм-коды, ИИ, движок, GPU)
let appSettings = {asr_backend: 'whisper_base', use_gpu: false, output_format: 'docx', timestamps: false, ai_enabled: false};
let backendList = [];       // [{id, kind, group, label, size_mb, description, installed}]
let installedModel = null;
let gemmaInstalled = false;
let gigaamDepsOk = false;
let gpuReport = null;
let activeDownload = null;   // {id, kind, model, label} | null
let downloadResolver = null; // {resolve, reject} — ожидающий startDownload()

function downloadBlockingRun() {
  return !!activeDownload && ['whisper', 'gigaam_deps', 'gigaam_model'].includes(activeDownload.kind);
}
const fileStatuses = {};   // имя -> status
const fileTexts = {};      // имя -> последний текст (черновик/улучшенный)
const fileBadges = {};     // имя -> бейдж
const fileChecked = {};    // имя -> отмечен ли галочкой для транскрибации
let llmDownloadCb = null;   // колбэк после успешной установки модели ИИ
let llmPollTimer = null;
let llmCancelRequested = false; // пользователь нажал «Отмена» во время скачивания
// ── Аудиоплеер окна «Документ» (спека specs/audio-player.md) ──
let audioEl = null;           // единый <audio> (не в DOM)
let tsIndex = [];             // [{seconds, el}] таймкоды текущего документа
let fileAudio = {};           // имя -> аудио готово (SSE file_status: audio_ok)
let apCurName = null;         // имя файла, чьё аудио сейчас в audioEl.src
let apActiveEl = null;        // текущий активный .ts-badge
let apPendingSeek = null;     // seek, отложенный до загрузки metadata
let apScrub = false;          // прогресс идёт от слайдера (не timeupdate)
let apLastUserScroll = 0;     // время последнего ручного скролла #result-text
let apIndex = -1;             // индекс активного абзаца в tsIndex (троттл)
let apFollow = true;          // автопрокрутка за активным абзацем
let apVolBefore = 0.5;        // уровень громкости до мьюта (для restore)
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
  if (files.length === 0) {
    openAlertModal();
    return;
  }
  fillModal('launch-overlay');
  openModal('launch-overlay');
  document.getElementById('launch-apply').focus();
}
function closeLaunchModal() { closeModal('launch-overlay'); }

// Сворачивание/разворачивание блока «Расширенные параметры» в модале запуска.
function toggleLaunchAdvanced() {
  const panel = document.getElementById('launch-advanced');
  const toggle = document.getElementById('launch-advanced-toggle');
  const arrow = document.getElementById('launch-advanced-arrow');
  if (!panel) return;
  const expanded = panel.style.display !== 'none';
  panel.style.display = expanded ? 'none' : '';
  if (toggle) toggle.setAttribute('aria-expanded', String(!expanded));
  if (arrow) arrow.textContent = expanded ? '▸' : '▾';
}

async function openSettingsModal() {
  await refreshModels();          // свежий статус модели ИИ → кнопка «Удалить модель» актуальна
  fillModal('settings-overlay');
  openModal('settings-overlay');
}
function closeSettingsModal() { closeModal('settings-overlay'); }

function modelOptions() {
  const byGroup = {};
  for (const b of backendList) {
    byGroup[b.group] = byGroup[b.group] || [];
    byGroup[b.group].push(b);
  }
  return Object.keys(byGroup)
    .map(group => {
      const body = byGroup[group].map(b => {
        const mark = b.installed ? ' ✓' : ` (~${b.size_mb} МБ)`;
        const desc = b.installed ? '' : ` — ${b.description}`;
        return `<option value="${b.id}">${b.label}${desc}${mark}</option>`;
      }).join('');
      return `<optgroup label="${group === 'gigaam' ? 'GigaAM v3 (русский)' : 'Whisper (многоязычный)'}">${body}</optgroup>`;
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
    model.value = currentBackend() || appSettings.asr_backend || 'whisper_base';
  }
  setGpuCheckbox(root);
  updateFormatHints();
}

async function deleteAiModel() {
  const ok = await confirmPopup('Удалить модель ИИ (gemma, ~3 ГБ) и отключить обработку с ИИ?');
  if (!ok) return;
  try {
    const resp = await fetch('/api/llm/delete', { method: 'POST' });
    const data = await resp.json();
    if (!data.ok) {
      addLog(`❌ Не удалось удалить модель: ${data.error || 'ошибка сервера'}`);
      return;
    }
    appSettings.ai_enabled = false;
    addLog('🗑 Модель ИИ удалена');
    await refreshModels();
    refreshLlmStatus();
    fillAiCheckboxes();
  } catch (err) {
    addLog(`❌ ${err.message}`);
  }
}

function fillAiCheckboxes() {
  for (const rootId of ['launch-overlay', 'settings-overlay']) {
    const ai = document.querySelector(`#${rootId} [data-field="ai"]`);
    if (ai) ai.checked = !!appSettings.ai_enabled;
  }
}

function currentBackend() {
  const bk = appSettings.asr_backend || 'whisper_base';
  return backendList.some(b => b.id === bk) ? bk : 'whisper_base';
}

function backendInfo(id) {
  return backendList.find(b => b.id === id) || null;
}

function collectModal(rootId) {
  const root = document.getElementById(rootId);
  const val = (f) => root.querySelector(`[data-field="${f}"]`);
  const out = {
    output_format: val('format') ? val('format').value : (appSettings.output_format || 'docx'),
    timestamps: val('timestamps') ? !!val('timestamps').checked : !!appSettings.timestamps,
    ai_enabled: val('ai') ? !!val('ai').checked : !!appSettings.ai_enabled,
    asr_backend: val('model') ? val('model').value : (appSettings.asr_backend || 'whisper_base'),
    use_gpu: val('gpu') ? !!val('gpu').checked : !!appSettings.use_gpu,
  };
  const trCb = val('translate');
  const trSel = val('translate-lang');
  if (trCb && trSel) {
    out.translate = !!trCb.checked;
    out.translate_lang = trSel.value || 'en';
    out.translate_name = trSel.selectedOptions && trSel.selectedOptions[0]
      ? trSel.selectedOptions[0].textContent
      : out.translate_lang;
  }
  return out;
}

// При включении чекбокса «Перевести» разблокируется выбор языка.
function syncLaunchTranslate() {
  const cb = document.querySelector('#launch-overlay [data-field="translate"]');
  const sel = document.querySelector('#launch-overlay [data-field="translate-lang"]');
  if (cb && sel) sel.disabled = !cb.checked;
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
    el.textContent = 'ГП: ошибка проверки';
    el.title = 'Не удалось проверить видеокарту. Обработка идёт на процессоре.';
  }
}

function refreshGpuStatusUI() {
  const el = document.getElementById('gpu-status');
  if (!gpuReport) {
    el.className = '';
    el.textContent = 'ГП: …';
    el.title = 'Проверка видеокарты…';
    return;
  }
  const onGpu = !!appSettings.use_gpu && !!gpuReport.cuda_available;
  el.className = onGpu ? 'gpu' : 'cpu';
  el.textContent = onGpu ? 'Используется ГП' : 'Используется ЦП';
  el.title = onGpu
    ? 'Распознавание речи выполняется на видеокарте NVIDIA.'
    : 'Распознавание речи выполняется на процессоре. Включите «Использовать GPU» в настройках, если есть видеокарта NVIDIA.';
}

async function refreshModels() {
  try {
    const data = await (await fetch('/api/models')).json();
    backendList = data.backends || [];
    installedModel = data.installed;
    gemmaInstalled = !!data.gemma_installed;
    gigaamDepsOk = !!data.gigaam_deps_ok;
    if (data.current) appSettings.asr_backend = data.current;
    syncAiModelButtons();
  } catch (_) {}
}

// Кнопки «Установить»/«Удалить» модель — взаимоисключающие,
// зависят от наличия установленной модели ИИ (gemma).
function syncAiModelButtons() {
  const install = document.getElementById('ai-install-row-btn');
  const del = document.getElementById('ai-delete-row-btn');
  if (install) install.style.display = gemmaInstalled ? 'none' : '';
  if (del) del.style.display = gemmaInstalled ? '' : 'none';
}

// Скачать модель ИИ (gemma) через общий модал загрузки модели.
// Успешную установку уже обрабатывает pollLlmInstall → refreshModels()
// (кнопка «Удалить»/«Установить» переключится сама).
function installAiModel() {
  offerLlmDownload(() => {
    refreshLlmStatus();
    fillAiCheckboxes();
  });
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
  const info = backendInfo(target);
  if (!info) return;
  const fallback = () => { sel.value = currentBackend(); };
  if (info.installed && !(info.kind === 'gigaam' && !gigaamDepsOk)) return;
  if (target === installedModel && info.kind === 'whisper') return;
  const cur = installedModel ? ` Текущая модель (${installedModel}) будет удалена.` : '';

  (async () => {
    try {
      if (info.kind === 'gigaam' && !gigaamDepsOk) {
        const ok = await confirmPopup(
          'GigaAM требует установки компонентов (torch CUDA + pyannote, ~2.5 ГБ). Продолжить?'
        );
        if (!ok) { fallback(); return; }
        await startDownload('gigaam_deps');
        gigaamDepsOk = true;
      }
      if (!info.installed && target !== installedModel) {
        const ok = await confirmPopup(`Скачать ${info.label} (~${info.size_mb} МБ)?${info.kind === 'whisper' ? cur : ' Текущая модель будет удалена.'}`);
        if (!ok) { fallback(); return; }
        await startDownload(info.kind === 'gigaam' ? 'gigaam_model' : 'whisper', target);
      }
      await refreshModels();
      sel.value = currentBackend();
    } catch (_) {
      fallback();
    }
  })();
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
      requestRestart();
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
          requestRestart();
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
      fileChecked[f.name] = true;
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
  delete fileChecked[name];
  delete fileAudio[name];
  // Удаляется файл, чьё аудио играет/загружено — глушим плеер и чистим кэш.
  if (apCurName === name && audioEl) {
    audioEl.pause();
    audioEl.removeAttribute('src');
    audioEl.load();
    apCurName = null;
  }
  if (taskId) {
    fetch(`/api/audio/remove/${taskId}/${encodeURIComponent(name)}`, { method: 'POST' }).catch(() => {});
  }
  files.splice(idx, 1);
  const wasCurrent = currentFileName === name;
  if (currentFileName === name) currentFileName = null;
  if (userPinnedFile === name) userPinnedFile = null;
  if (liveFile === name) liveFile = null;
  renderFileList();
  // перевести live-индикацию на оставшийся активный файл
  const next = firstActiveFile();
  if (next) {
    setLiveFile(next);
    document.getElementById('skip-btn').style.display = '';
  } else {
    document.getElementById('skip-btn').style.display = 'none';
    // удалён файл, который был показан в окне документа, и показывать больше нечего
    if (wasCurrent) {
      setResultText('');
      syncResultActions();
    }
  }
  syncAudioPlayer();
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
    const selected = f.name === currentFileName;
    const checked = fileChecked[f.name] !== false;
    return `<li class="file-item ${selected ? 'selected' : ''}"
      draggable="true"
      data-index="${i}"
      data-name="${escapeHtml(f.name)}" onclick="selectFile('${escapeJs(f.name)}')">
      <input type="checkbox" class="file-check" ${checked ? 'checked' : ''}
        onchange="toggleFileCheck('${escapeJs(f.name)}', this.checked)"
        onclick="event.stopPropagation()"
        title="Транскрибировать файл" aria-label="Транскрибировать файл">
      <span class="file-num">${i + 1}.</span>
      <span class="file-name">${escapeHtml(f.name)}</span>
      <span class="file-size">${formatSize(f.size)}</span>
      <span class="status status-${st}">${statusLabels[st] || 'В очереди'}</span>
      <span class="remove" onclick="event.stopPropagation(); removeFile(${i})" title="Убрать из списка" aria-label="Убрать из списка">✕</span>
    </li>`;
  }).join('');
  wireFileListDnd(list);
  updateSelectAll();
  syncResultActions();
}

/* ── Переупорядочивание файлов перетаскиванием ──────────────
   Паттерн из SortableJS/fwdtools: drag-картинка подменяется прозрачной
   заглушкой (призрака нет), исходная строка схлопывается, а позиция
   вставки вычисляется по средним точкам строк — placeholder
   вставляется между ними, и соседние строки сдвигаются. */

let dndDragEl = null;
let dndDragName = null;

function makePlaceholder(name) {
  const ph = document.createElement('li');
  ph.className = 'file-item dnd-placeholder';
  ph.dataset.name = name;
  return ph;
}

// Возвращает элемент, ПЕРЕД которым нужно вставить перетаскиваемую строку
// (первый, чья средняя точка ниже курсора).
function getDragAfterElement(list, y) {
  const items = [...list.querySelectorAll('li.file-item:not(.dragging)')];
  return items.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
}

function wireFileListDnd(list) {
  // Контейнерные обработчики вешаем один раз: renderFileList пересоздаёт
  // только строки (innerHTML), а list — постоянный элемент, иначе после
  // N рендеров накопилось бы N обработчиков.
  if (!list.dataset.dndWired) {
    list.dataset.dndWired = '1';
    // Позиционирование вешается на контейнер: это убирает мигание
    // placeholder'а, когда курсор переходит между строками.
    list.addEventListener('dragover', (e) => {
      if (!dndDragName) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const afterEl = getDragAfterElement(list, e.clientY);
      let ph = list.querySelector('.dnd-placeholder');
      if (!ph) ph = makePlaceholder(dndDragName);
      list.insertBefore(ph, afterEl);
    });
    list.addEventListener('dragleave', (e) => {
      if (!dndDragName) return;
      if (list.contains(e.relatedTarget)) return;
      removeDndSlot(list);
    });
    list.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!dndDragName) return;
      const ph = list.querySelector('.dnd-placeholder');
      if (ph && dndDragEl) {
        list.insertBefore(dndDragEl, ph);
        ph.remove();
      }
      reorderFromDnd(list);
    });
  }
  list.querySelectorAll('li.file-item').forEach((li) => {
    li.addEventListener('dragstart', (e) => {
      dndDragEl = li;
      dndDragName = li.dataset.name || null;
      removeDndSlot(list);
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', dndDragName || '');
      // призрак — точная непрозрачная копия исходной строки: тот же вид,
      // форма и цвет, только в виде плашки. fixed уводит клон из зоны
      // обрезки overflow у #file-list, иначе браузер не снимает его.
      const dragImage = li.cloneNode(true);
      dragImage.style.cssText =
        'position:fixed !important;left:-10000px !important;top:-10000px !important;' +
        'opacity:1 !important;pointer-events:none !important;margin:0 !important;';
      if (!li.classList.contains('selected')) {
        dragImage.style.background = 'var(--md-surface-container-high)';
      }
      list.appendChild(dragImage);
      // точка захвата в строке = точка привязки к курсору
      const rect = li.getBoundingClientRect();
      const grabX = Math.max(0, e.clientX - rect.left);
      const grabY = Math.max(0, e.clientY - rect.top);
      e.dataTransfer.setDragImage(dragImage, grabX, grabY);
      requestAnimationFrame(() => {
        dragImage.remove();
        li.classList.add('dragging');
      });
    });
    li.addEventListener('dragend', () => {
      cleanupDnd(list);
    });
  });
}

function removeDndSlot(list) {
  const slot = list.querySelector('.dnd-placeholder');
  if (slot) slot.remove();
}

// Новый порядок files берём прямо из DOM: после drop перетаскиваемая
// строка уже стоит на своём месте, placeholder удалён.
function reorderFromDnd(list) {
  removeDndSlot(list);
  const byName = new Map(files.map(f => [f.name, f]));
  const ordered = [];
  list.querySelectorAll('li.file-item').forEach((li) => {
    const name = li.dataset.name;
    if (name && byName.has(name)) ordered.push(byName.get(name));
  });
  if (ordered.length === files.length) files = ordered;
  cleanupDnd(list, true);
}

function cleanupDnd(list, keepSelection) {
  removeDndSlot(list);
  list.querySelectorAll('li.file-item.dragging').forEach(el => el.classList.remove('dragging'));
  dndDragEl = null;
  dndDragName = null;
  if (!keepSelection) return;
  renderFileList();
}

function toggleFileCheck(name, checked) {
  fileChecked[name] = checked;
  updateSelectAll();
}

function setAllChecked(checked) {
  for (const f of files) fileChecked[f.name] = checked;
  renderFileList();
}

function updateSelectAll() {
  const all = document.getElementById('file-select-all');
  if (!all) return;
  let cnt = 0;
  for (const f of files) if (fileChecked[f.name] !== false) cnt++;
  all.checked = files.length > 0 && cnt === files.length;
  all.indeterminate = files.length > 0 && cnt > 0 && cnt < files.length;
}

function selectFile(name) {
  if (!files.some(f => f.name === name)) return;
  currentFileName = name;
  userPinnedFile = name;
  renderFileList();
  setResultText(fileTexts[name] || '');
  syncAudioPlayer();
}

// Кнопки «Копировать» и «Открыть в редакторе» — появляются, когда у
// текущего файла есть готовый результат.
function syncResultActions() {
  const name = currentFileName;
  const hasDoc = !!fileTexts[name];
  const copy = document.getElementById('copy-btn');
  const openDoc = document.getElementById('open-doc-btn');
  const fontSize = document.getElementById('font-size-group');
  if (copy) copy.style.display = hasDoc ? '' : 'none';
  if (openDoc) openDoc.style.display = hasDoc ? '' : 'none';
  if (fontSize) fontSize.style.display = hasDoc ? '' : 'none';
}

async function openResultDoc() {
  if (!taskId || !currentFileName) return;
  try {
    const resp = await fetch(
      `/api/open-doc/${taskId}/${encodeURIComponent(currentFileName)}`,
      { method: 'POST' }
    );
    const data = await resp.json();
    if (data.error) {
      addLog(`❌ ${data.error}`);
    } else {
      addLog(`🖊 Открыт в редакторе: ${data.name}`);
    }
  } catch (err) {
    addLog(`❌ ${err.message}`);
  }
}

/* ── Загрузка и обработка ────────────────────────────────── */

async function runPipeline() {
  if (files.length === 0) return;
  if (downloadBlockingRun()) {
    addLog('⚠ Идёт скачивание модели — дождитесь завершения');
    return;
  }

  const p = launchParams && launchParams.asr_backend ? launchParams : appSettings;
  // Сервер шлёт два done, когда в запуске запрошен перевод: первое — как
  // только транскрибация готова, второе — после перевода результата.
  expectedDones = p.translate ? 2 : 1;
  // Галочки: отмеченные файлы транскрибируются, снятые — пропускаются.
  // Если не отмечен ни один — обрабатываем все.
  let selected = files.filter(f => fileChecked[f.name] !== false);
  if (selected.length === 0) selected = files;
  const formData = new FormData();
  for (const f of selected) {
    formData.append('files', f);
  }
  formData.append('ai', p.ai_enabled ? '1' : '0');
  enhanceMode = p.ai_enabled ? 'auto' : 'none';
  formData.append('output_format', p.output_format || 'docx');
  formData.append('timestamps', p.timestamps ? '1' : '0');
  formData.append('backend', p.asr_backend || 'whisper_base');
  formData.append('use_gpu', p.use_gpu ? '1' : '0');
  formData.append('translate', p.translate ? '1' : '0');
  if (p.translate) {
    formData.append('translate_lang', p.translate_lang || 'en');
    formData.append('translate_name', p.translate_name || '');
  }

  const ctx = document.getElementById('context-prompt').value.trim();
  if (ctx) formData.append('initial_prompt', ctx);

  setBusy(true);
  clearLog();
  addLog('=== PromptEar ===');
  showSpinner();
  resetResult();
  fileAudio = {};
  showLivePanel();

  // сбросить статусы: выбранные — в очередь, остальные — «Пропущено»,
// НО уже обработанные (есть текст) остаются «Готово». Текст и бейдж
// невыбранных файлов не трогаем — результат сохраняется при выборе.
  for (const f of files) {
    if (selected.includes(f)) {
      fileStatuses[f.name] = 'queued';
      fileTexts[f.name] = '';
      fileBadges[f.name] = 'Черновик';
    } else {
      fileStatuses[f.name] = fileTexts[f.name] ? 'done' : 'skipped';
      if (!fileTexts[f.name]) fileBadges[f.name] = 'Черновик';
    }
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

    case 'draft': {
      if (msg.filename && !files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      const dkey = msg.filename || liveFile;
      if (!dkey) break;
      // Текст всегда кладём под СВОЙ файл, иначе черновик активного файла
      // затирает текст закреплённого (currentFileName мог залипнуть).
      fileTexts[dkey] = msg.text;
      fileBadges[dkey] = 'Черновик (Whisper)';
      if (msg.filename && !followFile(msg.filename)) break;
      autoFollow(msg.filename);
      liveFile = liveFile || msg.filename || firstActiveFile();
      if (!liveFile) break;
      setLiveFile(liveFile);
      if (msg.final) {
        finishStreaming(msg.text);
      } else {
        setStreamingText(msg.text);
      }
      break;
    }

    case 'enhancing':
      showRefineProgress(msg.active_pass, msg.total_passes);
      if (msg.active_pass === 1) {
        // Проход 1 начался: черновик whisper уже показан мгновенно —
        // просто выравниваем окно по полному тексту.
        const draft = fileTexts[liveFile] || '';
        renderResultText(draft);
      } else if (lastPassText != null) {
        // предыдущий проход завершён — его полный текст мы уже получили,
        // плавно переходим: мигание → затухание → появление
        startPassTransition(lastPassText);
      }
      break;

    case 'enhancing_stream':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileTexts[msg.filename] = msg.text;
      fileBadges[msg.filename] = 'Обработка';
      if (!followFile(msg.filename)) { renderFileList(); break; }
      setLiveFile(msg.filename);
      // не печатаем по токенам: копим полный текст прохода,
      // он появится целиком на переходе к следующему проходу
      lastPassText = msg.text;
      break;

    case 'result':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileTexts[msg.filename] = msg.text;
      fileBadges[msg.filename] = msg.lang
        ? `Переведено (${msg.lang.toUpperCase()})`
        : 'Улучшено ИИ';
      if (!followFile(msg.filename)) { renderFileList(); break; }
      setLiveFile(msg.filename);
      hideRefineProgress();
      startPassTransition(msg.text);
      break;

    case 'file_status':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileStatuses[msg.filename] = msg.status;
      fileAudio[msg.filename] = !!msg.audio_ok;
      if (msg.status === 'transcribing') {
        autoFollow(msg.filename);
        document.getElementById('skip-btn').style.display = '';
      }
      renderFileList();
      if (msg.audio_ok && (currentFileName === msg.filename || followFile(msg.filename))) {
        syncAudioPlayer();
      }
      break;

    case 'skipped':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      fileStatuses[msg.filename] = 'skipped';
      fileBadges[msg.filename] = fileBadges[msg.filename] || 'Черновик';
      autoFollow(msg.filename);
      liveFile = liveFile || msg.filename;
      setLiveFile(liveFile);
      document.getElementById('skip-btn').style.display = 'none';
      renderFileList();
      break;

    case 'progress':
      if (!files.some(f => f.name === msg.filename)) break;  // файл удалён из списка
      autoFollow(msg.filename);
      liveFile = liveFile || msg.filename;
      setLiveFile(liveFile);
      break;

    case 'transcribing':
      hideRefineProgress();
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
      // При переводе в запуске сервер шлёт два done: первое — из pipeline
      // (транскрибация завершена), второе — после перевода. На первом done
      // НЕ закрываем EventSource, иначе событие result с переведённым
      // текстом не дойдёт до клиента и окно останется с русским черновиком.
      if (expectedDones > 1) {
        expectedDones = 1;
        if (enhanceMode !== 'auto') awaitSyncAskResult();
        break;
      }
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
    // Окно переключили на другой файл — показать ЕГО текст (или пусто),
    // иначе залипает текст предыдущего файла.
    setResultText(fileTexts[name] || '');
    renderFileList();
  }
  syncAudioPlayer();
}

// Следовать ли окну за событием файла: да, если пользователь явно не
// закрепил кликом другой файл.
function followFile(name) {
  if (!name) return true;
  return !userPinnedFile || userPinnedFile === name;
}

// Показать файл, за которым следим (активный файл задачи).
function autoFollow(name) {
  if (name && followFile(name)) setLiveFile(name);
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
  hideRefineProgress();
}

/* ── Help modal ─────────────────────────────────────────── */

function openHelp() {
  const check = document.getElementById('help-never-check');
  if (check) check.checked = helpSeen();
  document.getElementById('help-overlay').style.display = 'flex';
}

function closeHelp() {
  const check = document.getElementById('help-never-check');
  if (check && check.checked) {
    saveUiFlags(true, true);
  }
  document.getElementById('help-overlay').style.display = 'none';
}

/* ── Приветствие нового пользователя ───────────────────────
   При первом запуске показываем welcome-модалку, при закрытии
   переходим к инструкции. Флаги «не показывать» хранятся на
   сервере (settings.json): UI открывается на случайном порту
   127.0.0.1:<N>, origin меняется при каждом рестарте, и
   localStorage привязанный к origin терялся. */
function welcomeSeen() { return !!appSettings.ui_welcome_seen; }
function helpSeen() { return !!appSettings.ui_help_seen; }

function saveUiFlags(welcomeSeen, helpSeen) {
  appSettings.ui_welcome_seen = welcomeSeen;
  appSettings.ui_help_seen = helpSeen;
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ui_welcome_seen: welcomeSeen, ui_help_seen: helpSeen}),
  }).catch(() => {});
}

function showWelcomeIfNeeded() {
  if (!welcomeSeen()) {
    document.getElementById('welcome-overlay').style.display = 'flex';
  }
}

function dismissWelcome() {
  saveUiFlags(true, helpSeen());
  document.getElementById('welcome-overlay').style.display = 'none';
  if (!helpSeen()) openHelp();
}

/* ── Отображение текста в лайве ──
   Текст показывается как приходит от модели (батчи whisper, финалы) —
   мгновенно, без анимации печати. */

let lastPassText = null;   // полный текст последнего полученного прохода геммы

const TS_BADGE_RE = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;
let lastPlainResultText = '';  // последний отрисованный текст без HTML — для копирования

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Таймкоды [MM:SS]/[HH:MM:SS] рисуем чипсами-статусами; остальной текст
// экранируем перед вставкой в innerHTML.
function renderResultText(text) {
  lastPlainResultText = text;
  const el = document.getElementById('result-text');
  const esc = escapeHtml(text);
  el.innerHTML = esc.replace(TS_BADGE_RE, '<span class="ts-badge">$1</span>');
  rebuildTsIndex();
  el.scrollTop = el.scrollHeight;
}

function setStreamingText(text) {
  renderResultText(text);
}

function resetTyping() {
  lastPassText = null;
  if (passTransitionTimer) {
    clearTimeout(passTransitionTimer);
    passTransitionTimer = null;
  }
  const el = document.getElementById('result-text');
  if (el) el.classList.remove('pass-blink', 'pass-fade-out', 'pass-fade-in');
}

function finishStreaming(text) {
  renderResultText(text);
}

/* ── Live panel ──────────────────────────────────────────── */

function showLivePanel() {
  const el = document.getElementById('live-container');
  el.style.display = 'flex';
}

function resetResult() {
  currentFileName = null;
  liveFile = null;
  userPinnedFile = null;
  hideRefineProgress();
  setResultText('');
  resetAudioPlayer();
}

/* ── Аудиоплеер «Документа»: плэй/пауза/стоп, таймлайн,
      клик в текст → старт с абзаца, подсветка + автопрокрутка ── */

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function ensureAudioEl() {
  if (audioEl) return audioEl;
  audioEl = new Audio();
  audioEl.preload = 'auto';
  audioEl.volume = 0.5;
  audioEl.addEventListener('volumechange', syncAudioVolume);
  audioEl.addEventListener('loadedmetadata', () => {
    const dur = audioEl.duration;
    const slider = document.getElementById('ap-slider');
    if (slider && isFinite(dur)) {
      slider.max = String(Math.floor(dur));
      document.getElementById('ap-dur').textContent = fmtTime(dur);
    }
    if (apPendingSeek != null) {
      const t = apPendingSeek;
      apPendingSeek = null;
      try { audioEl.currentTime = t; } catch (_) {}
      audioEl.play();
      setPlayBtn(true);
    }
  });
  audioEl.addEventListener('timeupdate', syncAudioUi);
  audioEl.addEventListener('seeked', () => {
    apScrub = false;
    syncAudioUi();
  });
  audioEl.addEventListener('play', () => setPlayBtn(true));
  audioEl.addEventListener('pause', () => setPlayBtn(false));
  audioEl.addEventListener('ended', () => {
    setPlayBtn(false);
    syncAudioUi();
  });
  audioEl.addEventListener('error', () => {
    addLog('⚠ Ошибка воспроизведения аудио');
    setPlayBtn(false);
    hideAudioPlayer();
  });
  return audioEl;
}

function audioUrl(name) {
  return `/api/audio/${taskId}/${encodeURIComponent(name)}`;
}

function setPlayBtn(playing) {
  const btn = document.getElementById('ap-play');
  if (!btn) return;
  btn.classList.toggle('playing', !!playing);
  btn.title = playing ? 'Пауза' : 'Плэй';
  btn.setAttribute('aria-label', playing ? 'Пауза' : 'Плэй');
}

function syncAudioVolume() {
  const slider = document.getElementById('ap-volume');
  if (slider && audioEl) {
    slider.value = String(Math.round((audioEl.muted ? 0 : audioEl.volume) * 100));
  }
  const btn = document.getElementById('ap-volume-btn');
  if (btn && audioEl) {
    btn.classList.toggle('muted', !!(audioEl.muted || audioEl.volume === 0));
  }
}

function toggleAudioMute() {
  if (!audioEl) return;
  if (audioEl.muted || audioEl.volume === 0) {
    audioEl.muted = false;
    audioEl.volume = apVolBefore > 0 ? apVolBefore : 1;
  } else {
    apVolBefore = audioEl.volume;
    audioEl.volume = 0;
    audioEl.muted = true;
  }
  syncAudioVolume();
}

function setAudioVolume(val) {
  if (!audioEl) return;
  const v = Math.min(100, Math.max(0, Number(val) || 0)) / 100;
  if (v === 0) {
    audioEl.muted = true;
    audioEl.volume = 0;
  } else {
    audioEl.volume = v;
    if (audioEl.muted) audioEl.muted = false;
  }
  syncAudioVolume();
}

function resetAudioPlayer() {
  if (audioEl) {
    audioEl.pause();
    audioEl.removeAttribute('src');
    audioEl.load();
    audioEl.currentTime = 0;
  }
  apCurName = null;
  apPendingSeek = null;
  apFollow = true;
  apIndex = -1;
  setActiveBadge(null);
  hideAudioPlayer();
}

function showAudioPlayer() {
  const el = document.getElementById('audio-player');
  if (el) el.style.display = '';
  if (audioEl) {
    const slider = document.getElementById('ap-slider');
    if (slider) {
      slider.max = audioEl.duration && isFinite(audioEl.duration)
        ? String(Math.floor(audioEl.duration)) : '0';
      slider.value = String(Math.floor(audioEl.currentTime || 0));
    }
    document.getElementById('ap-cur').textContent = fmtTime(audioEl.currentTime || 0);
    document.getElementById('ap-dur').textContent = fmtTime(audioEl.duration || 0);
    setPlayBtn(!audioEl.paused);
  }
}

function hideAudioPlayer() {
  const el = document.getElementById('audio-player');
  if (el) el.style.display = 'none';
}

function syncAudioPlayer() {
  const name = currentFileName;
  const hasAudio = !!(taskId && name && fileAudio[name] && files.some(f => f.name === name));
  if (!hasAudio) {
    // Переключились на файл без аудио или его нет — гасим что играло.
    if (apCurName) resetAudioPlayer();
    else hideAudioPlayer();
    return;
  }
  showAudioPlayer();
  if (apCurName !== name) {
    if (audioEl) { audioEl.pause(); }
    apCurName = name;
    apPendingSeek = null;
    apFollow = true;
    apIndex = -1;
    setActiveBadge(null);
    const el = ensureAudioEl();
    el.src = audioUrl(name);
    el.load();
  }
}

function audioToggle() {
  const el = ensureAudioEl();
  if (!apCurName || !el.src) return;
  if (el.paused) { el.play(); setPlayBtn(true); }
  else { el.pause(); setPlayBtn(false); }
}

function audioStop() {
  if (!audioEl) return;
  audioEl.pause();
  try { audioEl.currentTime = 0; } catch (_) {}
  setPlayBtn(false);
  syncAudioUi();
}

function audioSeek(v) {
  if (!audioEl) return;
  apScrub = true;
  try { audioEl.currentTime = Number(v); } catch (_) {}
  document.getElementById('ap-cur').textContent = fmtTime(v);
  syncActiveParagraph();
}

function audioSeekTo(seconds) {
  const el = ensureAudioEl();
  if (!currentFileName || !fileAudio[currentFileName]) return;
  if (taskId && apCurName !== currentFileName) {
    if (audioEl) audioEl.pause();
    apCurName = currentFileName;
    el.src = audioUrl(currentFileName);
    el.load();
  }
  if (!apCurName) return;
  apFollow = true;         // клик в текст — явный запрос следования
  apLastUserScroll = 0;
  if (el.readyState >= 1 && isFinite(el.duration)) {
    try { el.currentTime = seconds; } catch (_) {}
    el.play();
    setPlayBtn(true);
  } else {
    apPendingSeek = seconds;
    el.load();
  }
}

function syncAudioUi() {
  const slider = document.getElementById('ap-slider');
  if (!slider || !audioEl || apScrub) return;
  const t = audioEl.currentTime || 0;
  slider.value = String(Math.floor(t));
  document.getElementById('ap-cur').textContent = fmtTime(t);
  syncActiveParagraph();
}

function syncActiveParagraph() {
  if (!audioEl || !tsIndex.length) return;
  const t = audioEl.currentTime || 0;
  let idx = -1;
  for (let i = 0; i < tsIndex.length; i++) {
    if (tsIndex[i].seconds <= t + 0.05) idx = i;
    else break;
  }
  if (idx < 0) idx = 0;
  if (idx !== apIndex) {
    apIndex = idx;
    const el = tsIndex[idx].el;
    setActiveBadge(el);
    const rt = document.getElementById('result-text');
    if (apFollow && rt && Date.now() - apLastUserScroll > 2000) {
      try { el.scrollIntoView({block: 'nearest', behavior: 'smooth'}); } catch (_) {}
    }
  }
}

function setActiveBadge(el) {
  if (apActiveEl && apActiveEl !== el) apActiveEl.classList.remove('active');
  apActiveEl = el;
  if (el) el.classList.add('active');
}

function badgeToSeconds(badge) {
  const parts = badge.textContent.split(':').map(Number);
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return 0;
}

function rebuildTsIndex() {
  tsIndex = [];
  const rt = document.getElementById('result-text');
  if (!rt) return;
  const badges = rt.querySelectorAll('.ts-badge');
  for (const el of badges) {
    tsIndex.push({seconds: badgeToSeconds(el), el});
  }
}

// Делегированный клик по документу: бейдж → его время, иначе → предыдущий.
function onDocClick(e) {
  if (!taskId || !currentFileName || !fileAudio[currentFileName]) return;
  if (!e.target.closest || !e.target.closest('#result-text')) return;
  const badge = e.target.closest('.ts-badge');
  let seconds;
  if (badge) {
    seconds = badgeToSeconds(badge);
  } else {
    // Клик в текст: геометрия проще document order — эвристика «предыдущий
    // бейдж, чья строка выше точки клика» по вертикали окна документа.
    seconds = 0;
    const y = e.clientY;
    for (const item of tsIndex) {
      const r = item.el.getBoundingClientRect();
      if (r.top <= y) seconds = item.seconds;
      else break;
    }
  }
  audioSeekTo(seconds);
}

function onDocScroll() {
  apLastUserScroll = Date.now();
}

/* ── Пресеты контекста: шаблоны по темам ───────────────── */
const CONTEXT_PRESETS = [
  { title: 'Интервью / подкаст', text: 'Тема: интервью (подкаст). Участники: ведущий и гость. Ведущий задаёт вопросы, гость подробно отвечает. Имена и термины: [ФИО гостя], [название проекта]. Диалог двух спикеров — реплики каждого в отдельный абзац.' },
  { title: 'Планёрка команды', text: 'Тема: рабочие планы команды на неделю. Участники: руководитель, исполнители, [имена]. Обсуждаются задачи, сроки, блокеры. Согласованные решения и ответственные выделять отдельно.' },
  { title: 'Лекция / вебинар', text: 'Тема: обучающая лекция (вебинар). Лектор объясняет материал последовательно и отвечает на вопросы слушателей. Термины: [профессиональные термины]. Сохранить учебную структуру: введение, основная часть, итоги.' },
  { title: 'Телефонный разговор', text: 'Тема: личный или деловой телефонный разговор двух человек. Участники: [имя1] и [имя2]. Материал — диалог: реплики каждого в отдельный абзац, паузы не значимы.' },
  { title: 'Переговоры с клиентом', text: 'Тема: переговоры (продажа, согласование условий). Участники: менеджер и клиент [имя]. Обсуждаются продукт [название], цена, сроки, условия. Зафиксировать обещания и достигнутые договорённости.' },
  { title: 'Конференция / доклад', text: 'Тема: выступление на конференции. Спикер представляет [тему доклада]. Термины: [ключевые термины]. Вопросы и ответы в конце — отдельным блоком.' },
  { title: 'Судебное / юридическое', text: 'Тема: судебное заседание или юридическая консультация. Участники: судья, стороны, юристы. Точность формулировок важнее всего. Юридические термины: [термины].' },
  { title: 'Приём врача', text: 'Тема: медицинская консультация. Участники: врач и пациент. Содержание: жалобы, анамнез, диагноз, назначения. Медицинские термины: [термины]. Назначения и рекомендации выделить отдельно.' },
  { title: 'Общее собрание', text: 'Тема: общее собрание / отчётный период. Участники: [список]. Отчёт о результатах [период], обсуждение вопросов. Итоги и принятые решения — в конце.' },
  { title: 'Мозговой штурм', text: 'Тема: обсуждение идей (мозговой штурм). Участники: [имена]. Идеи фиксируются без стилистической правки, сохраняя формулировки участников.' },
];

function buildContextPresets() {
  const menu = document.getElementById('presets-menu');
  if (!menu || menu.children.length) return;
  CONTEXT_PRESETS.forEach((p, i) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.onclick = () => applyContextPreset(i);
    const title = document.createElement('span');
    title.className = 'preset-title';
    title.textContent = p.title;
    btn.appendChild(title);
    li.appendChild(btn);
    menu.appendChild(li);
  });
}

function toggleContextPresets() {
  const menu = document.getElementById('presets-menu');
  if (!menu) return;
  buildContextPresets();
  menu.classList.toggle('hidden');
}

function applyContextPreset(index) {
  const preset = CONTEXT_PRESETS[index];
  const ta = document.getElementById('context-prompt');
  if (!preset || !ta) return;
  const cur = ta.value.trim();
  ta.value = cur ? cur + '\n\n' + preset.text : preset.text;
  document.getElementById('presets-menu')?.classList.add('hidden');
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('presets-menu');
  if (!menu || menu.classList.contains('hidden')) return;
  if (!e.target.closest('#presets-btn') && !e.target.closest('#presets-menu')) {
    menu.classList.add('hidden');
  }
});

/* ── Размер шрифта окна «Документ» ────────────────────── */
const DOC_FONT_MIN = 11;
const DOC_FONT_MAX = 24;
const DOC_FONT_DEFAULT = 14;

function loadDocFontSize() {
  try {
    const stored = parseInt(localStorage.getItem('promptear.docFontSize') || '', 10);
    if (Number.isFinite(stored)) {
      return Math.min(DOC_FONT_MAX, Math.max(DOC_FONT_MIN, stored));
    }
  } catch (_) {}
  return DOC_FONT_DEFAULT;
}

function applyDocFontSize() {
  const el = document.getElementById('result-text');
  if (!el) return;
  el.style.fontSize = loadDocFontSize() + 'px';
}

function changeDocFontSize(delta) {
  const next = Math.min(DOC_FONT_MAX, Math.max(DOC_FONT_MIN, loadDocFontSize() + delta));
  try {
    localStorage.setItem('promptear.docFontSize', String(next));
  } catch (_) {}
  applyDocFontSize();
}

function setResultText(text) {
  resetTyping();
  renderResultText(text);
}

function showRefineProgress(activePass, totalPasses) {
  const wrap = document.getElementById('refine-progress');
  wrap.style.display = 'flex';
  const bar = document.getElementById('refine-progress-bar');
  bar.style.width = `${Math.round((activePass / totalPasses) * 100)}%`;
  document.getElementById('refine-progress-label').textContent =
    `Проход ${activePass}/${totalPasses}`;
}

function hideRefineProgress() {
  document.getElementById('refine-progress').style.display = 'none';
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
      renderResultText(newText);
      el.classList.add('pass-fade-in');
      passTransitionTimer = setTimeout(() => {
        el.classList.remove('pass-fade-in');
        passTransitionTimer = null;
      }, 700);
    }, 1000);
  }, 5000);
}

function copyResult() {
  const text = lastPlainResultText || document.getElementById('result-text').textContent;
  if (!text) return;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => addLog('📋 Текст скопирован'));
  } else {
    addLog('⚠ Копирование недоступно');
  }
}

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
        const label = document.getElementById('llm-dl-label');
        const bar = document.getElementById('llm-dl-bar');
        const prog = st.install_progress;
        if (prog) {
          const total = prog.total_mb ? ` из ${prog.total_mb} МБ (${prog.pct}%)` : ' МБ';
          label.textContent = `Скачивание модели ИИ… ${prog.done_mb}${total}`;
          if (prog.total_mb) {
            bar.classList.remove('indeterminate');
            bar.style.width = `${Math.max(2, Math.min(100, prog.pct))}%`;
          } else {
            bar.classList.add('indeterminate');
          }
        } else {
          label.textContent = 'Скачивание модели ИИ… (может занять несколько минут)';
          bar.classList.add('indeterminate');
          bar.style.width = '40%';
        }
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
        refreshModels().then(() => { if (cb) cb(); });
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

async function awaitSyncAskResult() {
  if (!taskId) return;
  try {
    const resp = await fetch(`/api/results/${taskId}`);
    const data = await resp.json();
    const results = data.results || [];
    if (results.length > 0) {
      const first = results[0];
      // Не затираем переведённый/улучшенный текст: после перевода задача
      // приходит «done» повторно, и синк этой «догонялки» не должен
      // возвращать на экран исходный черновик вместо перевода.
      // Проверяем и до, и после fetch — fetch асинхронный, и за время
      // ожидания переведённый result мог уже дойти по SSE.
      const badge = (fileBadges[currentFileName] || '');
      const alreadyRefined = badge === 'Переведено (EN)' || badge === 'Переведено (RU)'
        || /^Переведено/.test(badge);
      if (alreadyRefined) return;
      currentFileName = first.filename;
      const b2 = (fileBadges[currentFileName] || '');
      if (/^Переведено/.test(b2)) return;
      fileTexts[currentFileName] = first.text;
      fileAudio[currentFileName] = !!first.audio_ok;
      fileBadges[currentFileName] = 'Черновик (Whisper)';
      if (first.text) {
        setResultText(first.text);
      }
      renderFileList();
      syncAudioPlayer();
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

async function copyLogs() {
  const text = document.getElementById('log').innerText;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  addLog('📋 Логи скопированы в буфер обмена');
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

function requestRestart() {
  fetch('/api/restart', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({open_settings: true}),
  }).catch(() => {});
}
  async function init() {
  initTheme();
  applyDocFontSize();
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
  // Перезапуск при смене GPU просил открыть настройки — выполняем.
  try {
    const st = await (await fetch('/api/startup', {cache: 'no-store'})).json();
    if (st && st.open_settings) openSettingsModal();
  } catch (_) {}
  showWelcomeIfNeeded();
}

async function refreshLlmStatus() {
  try {
    const llmResp = await fetch('/api/llm');
    const llm = await llmResp.json();
    const el = document.getElementById('llm-status');
    const errBox = document.getElementById('llm-error-box');
    const portField = document.querySelector(
      '#settings-overlay .modal-port-row'
    )?.closest('.modal-field');
    el.className = '';
    el.classList.remove('hidden');
    if (portField) portField.classList.remove('hidden');
    if (errBox) errBox.classList.add('hidden');
    if (llm.llm_ok && llm.model_ok) {
      el.textContent = 'ИИ Активен';
      el.title = 'Движок запущен, ИИ-модель скачана и работает. Улучшение текста и перевод доступны.';
    } else if (llm.model_ok && llm.port_ok) {
      el.classList.add('warn');
      el.textContent = 'ИИ Спит';
      el.title = 'Модель установлена, движок не запущен, но порт свободен — включится сам при первом «Улучшить с ИИ» или «Перевести».';
    } else {
      el.classList.add('red');
      el.textContent = 'ИИ не доступен';
      el.title = 'Движок ИИ не запущен: порт занят или модель не установлена.';
      if (errBox) errBox.classList.remove('hidden');
      document.getElementById('llm-error-text').textContent =
        llm.error ||
        (llm.model_ok
          ? 'Свободный порт для движка не найден — укажите порт в настройках.'
          : 'ИИ-модель не установлена.');
    }
  } catch (_) {
    const el = document.getElementById('llm-status');
    el.className = '';
    el.classList.remove('hidden');
    el.classList.add('red');
    el.textContent = 'ИИ не доступен';
    el.title = 'Не удалось получить статус ИИ. Проверьте подключение к серверу.';
  }
}

async function applyLlmPort(portArg) {
  const port = parseInt(portArg, 10);
  if (!port || isNaN(port)) return;
  try {
    const resp = await fetch('/api/llm/port', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({port}),
    });
    const data = await resp.json();
    if (data.ok) {
      document.getElementById('llm-error-box').classList.add('hidden');
      document.getElementById('llm-status').className = '';
      document.getElementById('llm-status').textContent = 'ИИ Активен';
      document.getElementById('llm-status').title =
        'Движок запущен, ИИ-модель скачана и работает. Улучшение текста и перевод доступны.';
      addLog(`✅ ИИ работает на порту ${data.port}`);
      refreshLlmStatus();
      return {ok: true, port: data.port};
    }
    document.getElementById('llm-error-text').textContent =
      data.error || 'Не удалось запустить ИИ на этом порту.';
    document.getElementById('llm-error-box').classList.remove('hidden');
    addLog(`⚠ ИИ: ${data.error || 'не удалось запустить'}`);
    return {ok: false, error: data.error};
  } catch (_) {
    document.getElementById('llm-error-text').textContent =
      'Ошибка соединения с сервером.';
    document.getElementById('llm-error-box').classList.remove('hidden');
    return {ok: false, error: 'Ошибка соединения с сервером.'};
  }
}

// Кнопка «Сменить порт в настройках» в подвале при «ИИ не готов»:
// открывает настройки и фокусирует поле LLM-порта.
function openSettingsPort() {
  openSettingsModal();
  setTimeout(() => {
    const portInput = document.querySelector(
      '#settings-overlay [data-field="llm-port"]'
    );
    if (portInput) { portInput.focus(); portInput.select(); }
  }, 100);
}

document.addEventListener('DOMContentLoaded', init);

// Подвал сам обновляет статус LLM: движок мог стартовать/упасть без действий
// пользователя — чтобы предупреждение не висело вечно и не вводило в заблуждение.
setInterval(refreshLlmStatus, 5000);

document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => e.preventDefault());

// Аудиоплеер: клик в текст документа → старт с абзаца; ручной скролл
// временно приостанавливает автопрокрутку за активным абзацем.
document.getElementById('result-text')?.addEventListener('click', onDocClick);
document.getElementById('result-text')?.addEventListener('scroll', onDocScroll, {passive: true});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    for (const id of ['launch-overlay', 'settings-overlay', 'confirm-overlay']) {
      const m = document.getElementById(id);
      if (m && m.style.display !== 'none') {
        closeModal(id);
        return;
      }
    }
    const welcome = document.getElementById('welcome-overlay');
    const help = document.getElementById('help-overlay');
    const logs = document.getElementById('logs-overlay');
    if (welcome.style.display !== 'none') {
      dismissWelcome();
    } else if (help.style.display !== 'none') {
      closeHelp();
    } else if (logs.style.display !== 'none') {
      closeLogs();
    }
  }
});