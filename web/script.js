/* PromptEar web UI — клиентская часть */

let files = [];
let taskId = null;
let eventSource = null;
let isDark = true;

function toggleTheme() {
  isDark = !isDark;
  document.body.classList.toggle('light', !isDark);
  document.getElementById('theme-toggle').textContent = isDark ? '☀️' : '🌙';
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
    }
  }
  renderFileList();
}

function removeFile(idx) {
  files.splice(idx, 1);
  renderFileList();
}

function renderFileList() {
  const container = document.getElementById('file-list-container');
  const list = document.getElementById('file-list');
  const dropText = document.getElementById('drop-text');

  if (files.length === 0) {
    container.style.display = 'none';
    dropText.textContent = 'Перетащите аудиофайлы сюда';
    return;
  }

  container.style.display = 'block';
  dropText.textContent = `Добавлено файлов: ${files.length}`;
  list.innerHTML = files.map((f, i) =>
    `<li><span>${f.name}</span><span class="remove" onclick="removeFile(${i})">✕</span></li>`
  ).join('');
}

/* ── Загрузка и обработка ────────────────────────────────── */

async function runPipeline() {
  if (files.length === 0) return;

  const formData = new FormData();
  for (const f of files) {
    formData.append('files', f);
  }
  formData.append('output_format', document.getElementById('output-format').value);

  const ctx = document.getElementById('context-prompt').value.trim();
  if (ctx) formData.append('initial_prompt', ctx);

  document.getElementById('run-btn').disabled = true;
  document.getElementById('run-btn').style.display = 'none';
  document.getElementById('cancel-btn').style.display = '';
  document.getElementById('log-container').style.display = 'flex';
  clearLog();
  addLog('=== PromptEar ===');
  setStatus('Загрузка файлов...');

  try {
    const resp = await fetch('/api/files', { method: 'POST', body: formData });
    const data = await resp.json();

    if (data.error) {
      addLog(`Ошибка: ${data.error}`);
      setStatus('Ошибка');
      finish();
      return;
    }

    taskId = data.task_id;
    addLog(`Отправлено ${data.file_count} файлов`);
    connectSSE(taskId);
  } catch (err) {
    addLog(`Ошибка: ${err.message}`);
    setStatus('Ошибка');
    finish();
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

    case 'progress':
      setStatus(`[${msg.current}/${msg.total}] ${msg.filename} — осталось ~${msg.eta}`);
      break;

    case 'transcribing':
      setStatus(msg.message);
      break;

    case 'busy':
      document.getElementById('run-btn').disabled = msg.busy;
      break;

    case 'ollama_ready':
      if (!msg.ollama_ok) {
        addLog('⚠ Ollama не найден. Улучшение текста отключено.');
      } else if (!msg.model_ok) {
        addLog('⚠ Модель Qwen не найдена. Улучшение может не работать.');
      } else {
        addLog('✅ Qwen доступен');
      }
      break;

    case 'done':
      addLog('✅ ' + msg.message);
      addLog('📁 Результаты сохранены в папке "output"');
      setStatus('Готово');
      finish();
      break;

    case 'error':
      addLog('❌ ' + msg.message);
      setStatus('Ошибка');
      finish();
      break;

    case 'cancelled':
      addLog('⛔ Отменено');
      setStatus('Отменено');
      finish();
      break;

    case '__done__':
      break;
  }
}

function cancelPipeline() {
  if (taskId) {
    fetch(`/api/cancel/${taskId}`, { method: 'POST' });
  }
}

function finish() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  document.getElementById('run-btn').disabled = false;
  document.getElementById('run-btn').style.display = '';
  document.getElementById('cancel-btn').style.display = 'none';
}

/* ── UI helpers ──────────────────────────────────────────── */

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

function setStatus(text) {
  document.getElementById('status-text').textContent = text;
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
    const ollamaResp = await fetch('/api/ollama');
    const ollama = await ollamaResp.json();
    const el = document.getElementById('ollama-status');
    if (ollama.ollama_ok) {
      el.textContent = `Qwen: ${ollama.model_ok ? '✅' : '⚠ модель не найдена'}`;
    } else {
      el.textContent = 'Ollama: не обнаружен';
    }
  } catch (_) {
    document.getElementById('ollama-status').textContent = 'Ollama: ошибка';
  }
}

document.addEventListener('DOMContentLoaded', init);
