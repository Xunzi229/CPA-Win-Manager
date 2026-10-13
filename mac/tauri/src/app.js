let state = null;
let activeTab = "cli";
let selected = {portable: "", package: ""};
let libraryTargetMode = "package";
let associateTargetId = null;
let installedAppsCache = [];
let librarySelectedRepo = null;
let libraryQueryId = 0;
let adminKeyRefreshing = false;
let adminKeySaving = false;
let adminKeyGeneration = 0;
let refreshInProgress = false;
let lastAutoRefreshTime = 0;
let postInstallTimer = null;
let postInstallCount = 0;
let lastJobAction = "";
let refreshGeneration = 0;
const pendingServiceActions = {};
const pendingButtonActions = new Map();
const busyButtonStates = new WeakMap();
const inlineReleases = {};
const inlineAssets = {};
const inlineLoading = {};
const usesNativeBridge = Boolean(window.__TAURI__ && window.__TAURI__.core);

async function invokeNative(command, args = {}) {
  try {
    const result = await window.__TAURI__.core.invoke(command, args);
    if (command !== "get_job" && result && result.error) throw new Error(result.error);
    return result;
  } catch (error) {
    throw new Error(error.message || String(error));
  }
}

async function openUrl(url) {
  if (!url) return;
  try {
    await api("/api/open", {path: url});
  } catch (_) {
    window.open(url, "_blank");
  }
}

function buttonActionKey(button) {
  if (button.tagName && button.tagName !== "BUTTON") return "";
  if (button.id) return `id:${button.id}`;
  const entries = Object.entries(button.dataset || {}).sort(([left], [right]) => left.localeCompare(right));
  return entries.length ? JSON.stringify(entries) : "";
}

function setButtonDisabled(button, disabled) {
  const busy = busyButtonStates.get(button);
  if (busy) busy.disabled = disabled;
  button.disabled = busy ? true : disabled;
}

function markButtonBusy(button, buttons) {
  if (!busyButtonStates.has(button)) {
    busyButtonStates.set(button, {disabled: Boolean(button.disabled)});
    buttons.add(button);
  }
  button.disabled = true;
  button.setAttribute?.("aria-busy", "true");
}

function syncBusyButtons() {
  document.querySelectorAll("button").forEach(button => {
    const buttons = pendingButtonActions.get(buttonActionKey(button));
    if (buttons) markButtonBusy(button, buttons);
  });
}

async function withBusyButton(button, action) {
  const key = buttonActionKey(button);
  if (button.disabled || (key && pendingButtonActions.has(key))) return;
  if (!key) return await action();
  const buttons = new Set();
  pendingButtonActions.set(key, buttons);
  markButtonBusy(button, buttons);
  try {
    return await action();
  } finally {
    pendingButtonActions.delete(key);
    buttons.forEach(current => {
      current.disabled = busyButtonStates.get(current).disabled;
      current.removeAttribute?.("aria-busy");
      busyButtonStates.delete(current);
    });
  }
}

function onBusyClick(button, action) {
  button.addEventListener("click", event => withBusyButton(button, () => action(event)));
}

function getToastContainer() {
  let container = document.getElementById("toast-container");
  if (!container && document.body) {
    if (typeof document.createElement === "function") {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className = "toast-container";
      document.body.appendChild(container);
    }
  }
  return container;
}

function showToast(message, type = null, duration = 3000) {
  const text = String(message ?? "").trim();
  if (!text) return;
  logLine(text);

  const container = getToastContainer();
  if (!container) return;

  if (!type) {
    if (/成功|已保存|已清理|已复制|已重置|已添加/.test(text)) {
      type = "success";
    } else if (/失败|错误|异常/.test(text)) {
      type = "error";
    } else if (/已被其他程序占用|已有任务正在执行|请先|不能为空|未找到|未检测到|不能/.test(text)) {
      type = "warning";
    } else {
      type = "info";
    }
  }

  if (container.dataset) container.dataset.lastToast = text;
  container.lastToast = { message: text, type };

  if (typeof document.createElement !== "function") {
    container.textContent = text;
    return;
  }

  const toast = document.createElement("div");
  toast.className = "toast toast-" + type;

  const iconMap = {
    success: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"/></svg>',
    error: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M2.343 13.657A8 8 0 1 1 13.657 2.343 8 8 0 0 1 2.343 13.657ZM6.03 4.97a.75.75 0 0 0-1.06 1.06L6.94 8 4.97 9.97a.75.75 0 1 0 1.06 1.06L8 9.06l1.97 1.97a.75.75 0 1 0 1.06-1.06L9.06 8l1.97-1.97a.75.75 0 0 0-1.06-1.06L8 6.94 6.03 4.97Z"/></svg>',
    warning: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M6.457 1.047c.659-1.234 2.427-1.234 3.086 0l6.082 11.378A1.75 1.75 0 0 1 14.082 15H1.918a1.75 1.75 0 0 1-1.543-2.575Zm1.763.707a.25.25 0 0 0-.44 0L1.698 13.132a.25.25 0 0 0 .22.368h12.164a.25.25 0 0 0 .22-.368Zm.53 3.996v2.5a.75.75 0 0 1-1.5 0v-2.5a.75.75 0 0 1 1.5 0ZM9 11a1 1 0 1 1-2 0 1 1 0 0 1 2 0Z"/></svg>',
    info: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Zm8-6.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM6.5 7.75A.75.75 0 0 1 7.25 7h1a.75.75 0 0 1 .75.75v2.75h.25a.75.75 0 0 1 0 1.5h-2.5a.75.75 0 0 1 0-1.5h.25v-2h-.25a.75.75 0 0 1-.75-.75ZM8 6a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z"/></svg>'
  };

  toast.innerHTML = '<span class="toast-icon">' + (iconMap[type] || "") + '</span>' +
    '<span class="toast-message">' + esc(text) + '</span>' +
    '<button type="button" class="toast-close" aria-label="关闭">&times;</button>';

  let timer = null;
  const closeToast = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    if (toast.classList.contains("toast-hide")) return;
    toast.classList.remove("toast-show");
    toast.classList.add("toast-hide");
    setTimeout(() => {
      if (toast.parentNode && typeof toast.parentNode.removeChild === "function") {
        toast.parentNode.removeChild(toast);
      }
    }, 260);
  };

  const closeBtn = toast.querySelector ? toast.querySelector(".toast-close") : null;
  if (closeBtn) closeBtn.onclick = closeToast;

  timer = setTimeout(closeToast, duration);
  toast.onmouseenter = () => { if (timer) { clearTimeout(timer); timer = null; } };
  toast.onmouseleave = () => { if (!timer) { timer = setTimeout(closeToast, 1200); } };

  if (typeof container.appendChild === "function") {
    container.appendChild(toast);
  }

  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => toast.classList.add("toast-show"));
  } else {
    setTimeout(() => toast.classList.add("toast-show"), 10);
  }
}

function showMessage(message, type = null) {
  showToast(message, type);
  return Promise.resolve();
}

function confirmAction(message, title = "操作确认") {
  if (typeof window !== "undefined" && window.__mockConfirm !== undefined) {
    return Promise.resolve(window.__mockConfirm);
  }
  const modal = document.getElementById("confirm-modal");
  if (!modal) {
    return Promise.resolve(typeof confirm === "function" ? confirm(message) : true);
  }

  const msgEl = document.getElementById("confirm-modal-message");
  const titleEl = document.getElementById("confirm-modal-title");
  const okBtn = document.getElementById("confirm-modal-ok");
  const cancelBtn = document.getElementById("confirm-modal-cancel");
  const closeBtn = document.getElementById("confirm-modal-close");

  if (titleEl) titleEl.textContent = title;
  if (msgEl) msgEl.textContent = message;

  if (okBtn) {
    if (/清空|移至废纸篓|移出|重置/.test(message)) {
      okBtn.className = "btn-danger";
    } else {
      okBtn.className = "btn-primary";
    }
  }

  modal.classList.remove("hidden");
  if (okBtn) okBtn.focus();

  return new Promise(resolve => {
    let resolved = false;
    const cleanup = () => {
      if (typeof document.removeEventListener === "function") {
        document.removeEventListener("keydown", onKeyDown);
      }
      modal.classList.add("hidden");
    };
    const finish = result => {
      if (resolved) return;
      resolved = true;
      cleanup();
      resolve(result);
    };

    const onKeyDown = event => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      } else if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      }
    };

    if (okBtn) okBtn.onclick = () => finish(true);
    if (cancelBtn) cancelBtn.onclick = () => finish(false);
    if (closeBtn) closeBtn.onclick = () => finish(false);
    modal.onclick = event => {
      if (event.target === modal) finish(false);
    };
    if (typeof document.addEventListener === "function") {
      document.addEventListener("keydown", onKeyDown);
    }
  });
}

async function promptText(message, secret = false) {
  if (!usesNativeBridge) return prompt(message);
  const modal = document.getElementById("input-modal");
  const input = document.getElementById("input-modal-value");
  document.getElementById("input-modal-message").textContent = message;
  input.value = "";
  input.type = secret ? "password" : "text";
  modal.classList.remove("hidden");
  input.focus();
  return new Promise(resolve => {
    const done = value => { modal.classList.add("hidden"); resolve(value); };
    document.getElementById("input-modal-ok").onclick = () => done(input.value);
    document.getElementById("input-modal-cancel").onclick = () => done(null);
    input.onkeydown = event => {
      if (event.key === "Enter") done(input.value);
      if (event.key === "Escape") done(null);
    };
  });
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function logLine(msg) {
  const logEl = document.getElementById("log");
  if (!logEl) return;
  const time = new Date().toTimeString().split(" ")[0];
  logEl.textContent += (logEl.textContent ? "\n" : "") + time + " " + msg;
  logEl.scrollTop = logEl.scrollHeight;
}

function repoProjectName(repo) {
  if (!repo || typeof repo !== "string") return "";
  let clean = repo.trim();
  clean = clean.split(/[?#]/)[0];
  clean = clean.replace(/\/+$/, "");
  clean = clean.replace(/\.git$/i, "");
  clean = clean.replace(/\/(releases|archive|tags|tree|blob)(\/.*)?$/i, "");
  clean = clean.replace(/^(https?:\/\/)?github\.com\//i, "");
  clean = clean.replace(/^git@github\.com:/i, "");
  const parts = clean.split("/").filter(Boolean);
  if (parts.length >= 2) return parts[1];
  if (parts.length === 1) return parts[0];
  return "";
}

function softwareDisplayName(row) {
  if (!row) return "";
  const installedName = (row.installed_name || "").trim();
  if (installedName) {
    return installedName;
  }
  if (row.installed_dir) {
    const base = row.installed_dir.replace(/\/+$/, "").split("/").pop() || "";
    const appName = base.replace(/\.app$/i, "").trim();
    if (appName) {
      return appName;
    }
  }
  const remoteProject = repoProjectName(row.repository);
  if (remoteProject) {
    return remoteProject;
  }
  return (row.name || "").trim() || "未命名软件";
}

async function api(path, body) {
  if (usesNativeBridge) {
    if (path === "/api/state") {
      return await invokeNative("get_state");
    }
    if (path.startsWith("/api/job/")) {
      return await invokeNative("get_job", {jobId: path.split("/")[3]});
    }
    const cmdMap = {
      "/api/settings": "save_settings",
      "/api/cancel": "cancel_job",
      "/api/choose-directory": "choose_directory",
      "/api/open": "open_path",
      "/api/macos/doctor": "macos_doctor",
      "/api/installed-apps": "scan_installed_apps",
      "/api/catalog": "get_catalog",
      "/api/manager/check-update": "check_manager_update"
    };
    if (cmdMap[path]) {
      return await invokeNative(cmdMap[path], body || {});
    }
    if (path.startsWith("/api/project/")) {
      const parts = path.split("/");
      const result = await invokeNative("project_action", {
        key: parts[3],
        action: parts[4].replaceAll("-", "_"),
        body: body || {}
      });
      if (parts[4] === "read-key" || parts[4] === "save-key" || parts[4] === "reset-key") return result;
      return result.job_id ? result : {...await api("/api/state"), ...result};
    }
    if (path.startsWith("/api/software/")) {
      const parts = path.split("/");
      const result = await invokeNative("software_action", {
        mode: parts[3],
        action: parts[4].replaceAll("-", "_"),
        body: body || {}
      });
      if (parts[4] === "release-options" || parts[4] === "release-assets") return result;
      return result.job_id ? result : {...await api("/api/state"), ...result};
    }
    throw new Error("原生应用不支持此接口：" + path);
  }

  const response = await fetch(path, {
    method: path === "/api/state" || path.startsWith("/api/job/") ? "GET" : "POST",
    headers: {"Content-Type": "application/json"},
    body: path === "/api/state" || path.startsWith("/api/job/") ? undefined : JSON.stringify(body || {})
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "操作失败");
  return data;
}

function projectCard(key) {
  const item = state.projects[key];
  if (!item) return "";
  const hasUp = Boolean(item.update);
  const isRunning = Boolean(item.is_running);

  let statusHtml = "";
  let buttonsHtml = "";
  if (pendingServiceActions[key]) {
    const actionText = {start: "启动", stop: "停止", restart: "重启"}[pendingServiceActions[key]];
    statusHtml = `<span class="service-status-text">正在${actionText}…</span>`;
    buttonsHtml = `<button type="button" disabled>正在${actionText}…</button>`;
  } else if (isRunning) {
    const sText = item.service || "运行中";
    const displayService = sText.startsWith("运行中") ? sText : `运行中 (${sText})`;
    statusHtml = `<span class="service-status-text service-status-running">${esc(displayService)}</span>`;
    buttonsHtml = `
      <button type="button" class="btn-success" data-open-page="${key}">打开后台</button>
      <button type="button" data-project="${key}" data-action="stop">停止</button>
      <button type="button" data-project="${key}" data-action="restart">重启</button>`;
  } else if ((item.port_info || (item.service && item.service.includes("占用"))) && !isRunning) {
    const p = item.port_info || {};
    let shortText = "";
    let fullPath = p.path || p.command || "";

    if (p.port && p.pid) {
      const procDesc = p.command || (p.path ? p.path.split("/").pop() : "");
      const procText = procDesc ? ` ${procDesc}` : "";
      shortText = `未运行（端口 ${p.port} 被其他程序占用：PID ${p.pid}${procText}）`;
    } else if (item.service) {
      const match = item.service.match(/端口\s*(\d+)\s*被其他程序占用[：:]\s*PID\s*(\d+)(?:[，,]\s*(.+?))?\s*）?$/);
      if (match) {
        const port = match[1];
        const pid = match[2];
        const rawLoc = match[3] || "";
        const procName = rawLoc ? rawLoc.split("/").pop() : "";
        shortText = `未运行（端口 ${port} 被其他程序占用：PID ${pid}${procName ? ` ${procName}` : ""}）`;
        if (!fullPath && rawLoc) fullPath = rawLoc;
      } else {
        shortText = item.service;
      }
    } else {
      shortText = "未运行（端口被其他程序占用）";
    }

    const detailHtml = fullPath
      ? `<div class="service-status-detail" title="${esc(fullPath)}">占用程序：${esc(fullPath)}</div>`
      : "";

    statusHtml = `
      <span class="service-status-text service-status-alert">${esc(shortText)}</span>
      ${detailHtml}`;
    buttonsHtml = `
      <button type="button" class="btn-success" data-project="${key}" data-action="start">启动</button>
      <button type="button" disabled>停止</button>
      <button type="button" disabled>重启</button>`;
  } else {
    statusHtml = `<span class="service-status-text service-status-stopped">${esc(item.service || "未运行")}</span>`;
    buttonsHtml = `
      <button type="button" class="btn-success" data-project="${key}" data-action="start">启动</button>
      <button type="button" disabled>停止</button>
      <button type="button" disabled>重启</button>`;
  }

  if (key === "cli") {
    const brewMissing = Boolean(item.brew_missing);
    let brewWarningHtml = "";
    if (brewMissing) {
      brewWarningHtml = `
        <div class="system-status-banner warning" style="margin: 10px 0; padding: 10px 14px; border-radius: 6px; background: #fffbe6; border: 1px solid #ffe58f; color: #d46b08; font-size: 13px;">
          ⚠️ 未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：<a href="https://brew.sh" target="_blank" class="link-text">https://brew.sh</a>
        </div>
      `;
    }

    const actionButton = item.installed
      ? `<button type="button" class="btn-primary" data-project="cli" data-action="update">更新</button>`
      : `<button type="button" class="btn-primary" data-project="cli" data-action="install">安装</button>`;

    return `
      <div class="info-line">
        <span class="info-label">项目主页：</span>
        <a href="${esc(item.repo)}" target="_blank" class="link-text">${esc(item.repo)}</a>
      </div>

      ${brewWarningHtml}

      <div class="info-line" style="margin: 10px 0;">
        <span>本地版本：${esc(item.local || "尚未安装")}</span>
        <span style="margin-left: 20px;">最新版本（缓存）：${esc(item.latest || "尚未检查")}</span>
        ${hasUp ? '<span class="red-indicator">◆ 有新版本</span>' : ''}
      </div>

      <div class="service-card">
        <div class="service-status-info">${statusHtml}</div>
        <div class="service-buttons">${buttonsHtml}</div>
      </div>

      <div class="meta-hint">macOS 下通过 Homebrew 管理服务。主配置文件为 ~/.cli-proxy-api/config.yaml（软链至 $(brew --prefix)/etc/cliproxyapi.conf）。首次使用前请在配置文件中配置上游账号。</div>

      <div class="row-flex" style="margin-top: 14px;">
        <button type="button" class="btn-default" data-project="cli" data-action="check">检查最新版</button>
        ${actionButton}
        ${hasUp ? '<span class="red-indicator">◆ 有新版本</span>' : ''}
      </div>

      <div class="meta-hint" style="margin-top: 12px;">可检查版本，或通过 Homebrew 安装与管理服务。更新命令：npx cliproxyapi-brew-updater。</div>
    `;
  }

  let adminKeyHtml = "";
  if (key === "plus") {
    adminKeyHtml = `
      <div class="key-card">
        <h4 class="card-box-title" style="margin-bottom: 4px;">管理员登录 Key</h4>
        <div class="key-row">
          <input type="text" id="plus-admin-key" readonly value="${esc(item.admin_key || '')}" placeholder="未找到明文 Key，请录入已有 Key" class="key-input">
          <button type="button" id="plus-copy-key" class="btn-primary" data-copy-key="${esc(item.admin_key || '')}" ${item.admin_key ? "" : "disabled"}>复制 Key</button>
          <button type="button" data-refresh-key="plus">重新读取 Key</button>
          <button type="button" data-enter-key="plus">录入已有 Key</button>
          <button type="button" class="btn-default" data-reset-key="plus">重置 Key</button>
        </div>
        <div class="meta-hint" id="plus-key-note">${esc(item.admin_key_note || "仅首次初始化时日志会输出新 Key；录入已有 Key 只保存副本，不修改服务登录凭据。")}</div>
      </div>`;
  }

  const backupCount = item.backup_count || 0;
  const backupText = backupCount > 0 ? `清空安装备份 (${backupCount})` : "清空安装备份";
  const upgradeDisabled = !hasUp ? "disabled" : "";

  return `
    <div class="info-line">
      <span class="info-label">项目主页：</span>
      <a href="${esc(item.repo)}" target="_blank" class="link-text">${esc(item.repo)}</a>
    </div>

    <div class="row-flex" style="margin: 10px 0;">
      <span class="info-label" style="white-space: nowrap;">解压目录：</span>
      <input type="text" id="dir-${key}" class="input-flex" value="${esc(item.directory)}" readonly title="双击后可手动输入路径">
      <button type="button" class="btn-default" data-browse="${key}">选择文件夹</button>
    </div>

    <div class="info-line">
      <span>本地版本：${esc(item.local || "尚未安装")}</span>
      <span style="margin-left: 20px;">最新版本（缓存）：${esc(item.latest || "尚未检查")}</span>
      ${hasUp ? '<span class="red-indicator">◆ 有新版本</span>' : ''}
    </div>

    <div class="service-card">
      <div class="service-status-info">${statusHtml}</div>
      <div class="service-buttons">${buttonsHtml}</div>
    </div>

    ${adminKeyHtml}

    <div class="meta-hint">首次安装自动创建本机监听配置；使用前请在 ${esc(item.config || "config.yaml")} 配置上游账号。</div>
    <div class="meta-hint">解压目录自动保存</div>

    <div class="row-flex" style="margin-top: 14px;">
      <button type="button" class="btn-default" data-project="${key}" data-action="check">检查最新版</button>
      <button type="button" class="btn-primary" data-project="${key}" data-action="install" ${item.installed ? upgradeDisabled : ""}>${item.installed ? "升级" : "安装"}</button>
      <button type="button" class="btn-default" data-project="${key}" data-action="clear_backups">${backupText}</button>
      ${hasUp ? '<span class="red-indicator">◆ 有新版本</span>' : ''}
    </div>

    <div class="meta-hint" style="margin-top: 12px;">可检查版本，或在解压目录安装最新版。</div>
  `;
}

function portablePanel() {
  const rows = state.portable || [];
  const selectedRow = rows.find(r => r.id === selected.portable) || rows[0] || null;
  if (!selected.portable && selectedRow) selected.portable = selectedRow.id;

  const trs = rows.map(row => {
    const isSel = selected.portable === row.id ? "selected" : "";
    const hasUp = Boolean(row.update);
    const dirDisplay = (hasUp ? '<span class="red-indicator">↑ </span>' : '') + esc(row.directory);

    return `
      <tr data-id="${esc(row.id)}" data-mode="portable" class="${isSel}">
        <td style="max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${dirDisplay}</td>
        <td>
          <div class="row-repo-cell">
            <input type="text" id="repo-portable-${esc(row.id)}" class="table-input row-repo-input" data-row-repo="portable" data-id="${esc(row.id)}" value="${esc(row.repository)}" title="${esc(row.repository)}" placeholder="输入 GitHub 地址" spellcheck="false" autocomplete="off">
            <a href="${esc(row.repository)}" target="_blank" rel="noopener noreferrer" class="link-btn repo-open-link" title="在浏览器中打开" tabindex="-1">↗</a>
          </div>
        </td>
        <td>${esc(row.local || "-")}</td>
        <td>${esc(row.latest || "尚未检查")}</td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs" data-row-check="portable" data-id="${esc(row.id)}">检查</button></td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs ${hasUp ? 'btn-primary' : ''}" data-row-install="portable" data-id="${esc(row.id)}">${hasUp ? '升级' : '安装 / 更新'}</button></td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs" data-row-open="portable" data-dir="${esc(row.directory)}">打开目录</button></td>
        <td style="text-align: center;"><button type="button" class="btn-danger btn-xs" data-row-clear-backup="portable" data-id="${esc(row.id)}">清空安装备份</button></td>
      </tr>
    `;
  }).join("");

  return `
    <div class="row-space" style="margin-bottom: 10px;">
      <div class="row-flex">
        <button type="button" class="btn-primary" id="btn-portable-add-dir">选择解压目录添加</button>
        <button type="button" class="btn-default" data-check-all="portable">全部检查</button>
        <button type="button" class="btn-default" data-remove="portable">移除记录</button>
      </div>
      <button type="button" class="btn-default" data-open-library="portable">软件库</button>
    </div>

    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 240px;">软件安装目录</th>
            <th>GitHub 地址</th>
            <th style="width: 100px;">本地版本</th>
            <th style="width: 110px;">最新版本</th>
            <th style="width: 70px; text-align: center;">检查</th>
            <th style="width: 95px; text-align: center;">安装 / 更新</th>
            <th style="width: 85px; text-align: center;">打开目录</th>
            <th style="width: 110px; text-align: center;">清空安装备份</th>
          </tr>
        </thead>
        <tbody>
          ${trs || '<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 20px;">暂无免安装软件记录，请点击上方“选择解压目录添加”或从“软件库”选择。</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="row-edit-box">
      <div class="row-flex">
        <span class="info-label" style="width: 120px;">GitHub 地址：</span>
        <input type="text" id="portable-edit-repo" class="input-flex" value="${esc(selectedRow ? selectedRow.repository : '')}">
      </div>
      <div class="row-flex">
        <span class="info-label" style="width: 120px;">额外保留文件 / 目录：</span>
        <input type="text" id="portable-edit-keep" class="input-flex" placeholder="选填，例如 config;data" value="${esc(selectedRow ? selectedRow.preserve : '')}">
      </div>
      <div class="row-flex">
        <span class="info-label" style="width: 120px;">对应包：</span>
        <input type="text" id="portable-edit-asset" class="input-flex" readonly value="${esc(selectedRow ? selectedRow.asset : '')}" placeholder="点击右侧检查后自动匹配">
        <button type="button" class="btn-primary" id="btn-portable-fetch-asset">检查 / 获取附件</button>
      </div>
    </div>
  `;
}

function packagePanel() {
  const rows = state.packages || [];
  const selectedRow = rows.find(r => r.id === selected.package) || rows[0] || null;
  if (!selected.package && selectedRow) selected.package = selectedRow.id;

  const trs = rows.map(row => {
    const isSel = selected.package === row.id ? "selected" : "";
    const hasUp = Boolean(row.update);
    const displayName = softwareDisplayName(row);
    const nameDisplay = (hasUp ? '<span class="red-indicator">↑ </span>' : '') + esc(displayName);
    const localVer = row.installed_version || (row.installed_dir ? "未知" : row.local || "未关联");

    return `
      <tr data-id="${esc(row.id)}" data-mode="package" class="${isSel}">
        <td><strong>${nameDisplay}</strong></td>
        <td>
          <div class="row-repo-cell">
            <input type="text" id="repo-package-${esc(row.id)}" class="table-input row-repo-input" data-row-repo="package" data-id="${esc(row.id)}" value="${esc(row.repository)}" title="${esc(row.repository)}" placeholder="输入 GitHub 地址" spellcheck="false" autocomplete="off">
            <a href="${esc(row.repository)}" target="_blank" rel="noopener noreferrer" class="link-btn repo-open-link" title="在浏览器中打开" tabindex="-1">↗</a>
          </div>
        </td>
        <td><a href="javascript:void(0)" class="link-text" data-associate-app="${esc(row.id)}">${esc(localVer)}</a></td>
        <td>
          <select id="pkg-version-${esc(row.id)}" class="table-select row-version-select" data-row-version="package" data-id="${esc(row.id)}" title="直接选择目标版本或跟随最新版">
            ${renderInlineVersionOptions(row)}
          </select>
        </td>
        <td>
          <select id="pkg-asset-${esc(row.id)}" class="table-select row-asset-select" data-row-asset="package" data-id="${esc(row.id)}" title="${esc(row.selected_asset || row.asset || "选择安装附件")}">
            ${renderInlineAssetOptions(row)}
          </select>
        </td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs" data-row-check="package" data-id="${esc(row.id)}">检查</button></td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs ${hasUp ? 'btn-primary' : ''}" data-row-install="package" data-id="${esc(row.id)}">安装</button></td>
        <td style="text-align: center;"><button type="button" class="btn-danger btn-xs" data-row-uninstall="package" data-id="${esc(row.id)}" ${row.installed_dir ? "" : "disabled"}>移至废纸篓</button></td>
      </tr>
    `;
  }).join("");

  return `
    <div class="row-flex" style="margin-bottom: 10px; flex-wrap: wrap;">
      <button type="button" class="btn-default" data-check-all="package">全部检查</button>
      <button type="button" class="btn-default" id="refresh-installed-btn">刷新本地版本</button>
      <button type="button" class="btn-primary" data-upgrade-all="package">下载全部待更新</button>
      <button type="button" class="btn-default" data-clean-all-downloads>清空全部下载包</button>
      <button type="button" class="btn-default" data-open-download-dir>打开下载目录</button>
      <button type="button" class="btn-default" data-remove="package">移除记录</button>
    </div>

    <div class="row-flex" style="margin: 8px 0 6px;">
      <span class="info-label">GitHub 地址：</span>
      <input type="text" id="add-package-input" placeholder="输入 GitHub 地址，例如 https://github.com/localsend/localsend" class="input-flex">
      <button type="button" class="btn-primary" id="btn-add-package">添加软件</button>
      <button type="button" class="btn-default" data-open-library="package">软件库</button>
    </div>

    <div class="meta-hint">本地版本来自 macOS 已安装软件记录；点击版本可关联软件。安装按钮下载并打开安装包，由 macOS 安装向导完成安装。</div>

    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 150px;">软件</th>
            <th>GitHub 地址</th>
            <th style="width: 120px;">本地安装版本</th>
            <th style="width: 140px;">最新发布版本</th>
            <th style="width: 230px;">安装附件</th>
            <th style="width: 70px; text-align: center;">检查</th>
            <th style="width: 70px; text-align: center;">安装</th>
            <th style="width: 70px; text-align: center;">卸载</th>
          </tr>
        </thead>
        <tbody>
          ${trs || '<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 20px;">暂无安装向导软件记录，请输入 GitHub 地址添加或从软件库选用。</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="meta-hint">在表格列中可直接切换目标版本与安装附件；批量下载遵循已保存的选择，不自动降级；卸载不删除个人配置。</div>
    <div class="meta-hint">填写 GitHub 地址添加软件，或选择一行检查、安装、更新。</div>
  `;
}

function renderInlineVersionOptions(row) {
  const cached = inlineReleases[row.id];
  const releases = cached?.releases || [];
  const selectedTag = row.selected_tag || "";
  let html = '<option value="">' + esc(row.latest || "最新") + "</option>";
  if (selectedTag && !releases.some(r => r.tag === selectedTag)) {
    html += '<option value="' + esc(selectedTag) + '" selected>' + esc(selectedTag) + "（固定）</option>";
  }
  releases.forEach(r => {
    if (r.tag === row.latest && !r.prerelease && selectedTag !== r.tag) return;
    const isSel = r.tag === selectedTag ? "selected" : "";
    html += '<option value="' + esc(r.tag) + '" ' + isSel + ">" + esc(r.tag) + (r.prerelease ? "（预发布）" : "") + "</option>";
  });
  if (cached?.has_more) {
    html += '<option value="__more__">加载更多历史版本…</option>';
  }
  return html;
}

function renderInlineAssetOptions(row, tag, chosenAsset) {
  const activeTag = tag !== undefined ? tag : (row.selected_tag || row.latest || "");
  const cached = inlineAssets[row.id]?.[activeTag];
  const assets = cached?.assets || [];
  const selectedAsset = chosenAsset !== undefined ? chosenAsset : (row.selected_asset || "");
  const autoName = cached?.recommended || row.asset || "";
  const defaultLabel = autoName ? esc(autoName) : "推荐附件";
  let html = '<option value="">' + defaultLabel + "</option>";
  if (selectedAsset && !assets.some(a => a.name === selectedAsset)) {
    html += '<option value="' + esc(selectedAsset) + '" selected>' + esc(selectedAsset) + "</option>";
  }
  assets.forEach(a => {
    const isSel = a.name === selectedAsset ? "selected" : "";
    const sizeText = a.size ? " · " + (a.size / 1048576).toFixed(1) + " MB" : "";
    html += '<option value="' + esc(a.name) + '" ' + isSel + ">" + esc(a.name) + sizeText + "</option>";
  });
  return html;
}

async function preloadRowOptions(id, repository, tag) {
  if (inlineLoading[id]) return;
  inlineLoading[id] = true;
  try {
    if (!inlineReleases[id]) {
      const res = await api("/api/software/package/release-options", { id, repository, page: 1 });
      inlineReleases[id] = res;
      const vSelect = document.getElementById("pkg-version-" + id);
      const row = (state.packages || []).find(p => p.id === id);
      if (vSelect && row && document.activeElement !== vSelect) {
        vSelect.innerHTML = renderInlineVersionOptions(row);
        vSelect.value = row.selected_tag || "";
      }
    }
    const currentTag = tag || (state.packages || []).find(p => p.id === id)?.selected_tag || (state.packages || []).find(p => p.id === id)?.latest || "";
    if (currentTag && (!inlineAssets[id] || !inlineAssets[id][currentTag])) {
      const aRes = await api("/api/software/package/release-assets", { id, repository, tag: currentTag });
      if (!inlineAssets[id]) inlineAssets[id] = {};
      inlineAssets[id][currentTag] = aRes;
      const aSelect = document.getElementById("pkg-asset-" + id);
      const row = (state.packages || []).find(p => p.id === id);
      if (aSelect && row && document.activeElement !== aSelect) {
        aSelect.innerHTML = renderInlineAssetOptions(row, currentTag);
        aSelect.value = row.selected_asset || "";
      }
    }
  } catch (_) {
  } finally {
    delete inlineLoading[id];
  }
}

function preloadPackageOptions() {
  if (!state?.packages || activeTab !== "package") return;
  state.packages.forEach(row => {
    preloadRowOptions(row.id, row.repository, row.selected_tag || row.latest || "");
  });
}

async function handleInlineVersionChange(event) {
  const select = event.target;
  const id = select.dataset.id;
  const row = (state.packages || []).find(p => p.id === id);
  if (!row) return;

  if (select.value === "__more__") {
    const prevVal = row.selected_tag || "";
    select.disabled = true;
    try {
      const currentPage = inlineReleases[id]?.page || 1;
      const res = await api("/api/software/package/release-options", {
        id, repository: row.repository, page: currentPage + 1
      });
      if (!inlineReleases[id]) inlineReleases[id] = { releases: [], page: 1, has_more: false };
      const seen = new Set(inlineReleases[id].releases.map(r => r.tag));
      inlineReleases[id].releases.push(...res.releases.filter(r => !seen.has(r.tag)));
      inlineReleases[id].page = res.page;
      inlineReleases[id].has_more = res.has_more;
      select.innerHTML = renderInlineVersionOptions(row);
      select.value = prevVal;
      logLine(softwareDisplayName(row) + "：已加载更多历史版本（第 " + res.page + " 页）。");
    } catch (error) {
      logLine(softwareDisplayName(row) + "：加载历史版本失败：" + error.message);
      select.innerHTML = renderInlineVersionOptions(row);
      select.value = prevVal;
    } finally {
      select.disabled = false;
    }
    return;
  }

  const newTag = select.value;
  const assetSelect = document.getElementById("pkg-asset-" + id);
  select.disabled = true;
  if (assetSelect) assetSelect.disabled = true;

  try {
    let assetsInfo = inlineAssets[id]?.[newTag];
    if (!assetsInfo && newTag) {
      assetsInfo = await api("/api/software/package/release-assets", {
        id, repository: row.repository, tag: newTag
      });
      if (!inlineAssets[id]) inlineAssets[id] = {};
      inlineAssets[id][newTag] = assetsInfo;
    }

    const prevAsset = row.selected_asset || "";
    const targetAsset = (prevAsset && assetsInfo?.assets?.some(a => a.name === prevAsset)) ? prevAsset : "";

    const result = await api("/api/software/package/select-release", {
      id, repository: row.repository, tag: newTag, asset: targetAsset
    });
    state = result;
    render();
    logLine(softwareDisplayName(row) + "：已切换目标版本为 " + (newTag || "最新版") + "。");
  } catch (error) {
    logLine(softwareDisplayName(row) + "：切换版本失败：" + error.message);
    showMessage("切换版本失败：" + error.message);
    render();
  }
}

async function handleInlineAssetChange(event) {
  const select = event.target;
  const id = select.dataset.id;
  const row = (state.packages || []).find(p => p.id === id);
  if (!row) return;

  const newAsset = select.value;
  const versionSelect = document.getElementById("pkg-version-" + id);
  const currentTag = versionSelect ? versionSelect.value : (row.selected_tag || "");

  select.disabled = true;
  if (versionSelect) versionSelect.disabled = true;

  try {
    const result = await api("/api/software/package/select-release", {
      id, repository: row.repository, tag: currentTag, asset: newAsset
    });
    state = result;
    render();
    logLine(softwareDisplayName(row) + "：已切换安装附件为 " + (newAsset || "推荐附件") + "。");
  } catch (error) {
    logLine(softwareDisplayName(row) + "：切换附件失败：" + error.message);
    showMessage("切换附件失败：" + error.message);
    render();
  }
}

let savingRowRepo = false;
async function handleRowRepoChange(event) {
  const input = event.target;
  const mode = input?.dataset?.rowRepo;
  const id = input?.dataset?.id;
  if (!mode || !id || savingRowRepo) return;
  const list = mode === "portable" ? (state.portable || []) : (state.packages || []);
  const row = list.find(item => item.id === id);
  if (!row) return;

  const newRepo = (input.value || "").trim();
  if (!newRepo) {
    showMessage("GitHub 地址不能为空。");
    input.value = row.repository;
    return;
  }
  if (newRepo === row.repository) {
    return;
  }

  savingRowRepo = true;
  try {
    const res = await api(`/api/software/${mode}/edit`, { id, repository: newRepo });
    if (res && res.error) throw new Error(res.error);
    delete inlineReleases[id];
    delete inlineAssets[id];
    delete inlineLoading[id];
    state = res;
    render();
    logLine(`[${mode === "portable" ? "免安装软件" : "安装向导软件"}] 已更新 GitHub 地址为：${newRepo}`);
  } catch (error) {
    input.value = row.repository;
    showMessage(error.message || "更新 GitHub 地址失败");
  } finally {
    savingRowRepo = false;
  }
}

function render() {
  if (!state) return;

  const pEnabled = Boolean(state.proxy_enabled);
  document.getElementById("proxy-badge").textContent = pEnabled ? "代理：已启用" : "代理：直连";
  document.getElementById("header-version-text").textContent = `管理器版本: v${state.version || '1.5.16'}`;

  const cliHasUp = state.projects && state.projects.cli && state.projects.cli.update;
  const plusHasUp = state.projects && state.projects.plus && state.projects.plus.update;
  const portHasUp = (state.portable || []).some(r => r.update);
  const pkgHasUp = (state.packages || []).some(r => r.update);

  document.getElementById("dot-cli").classList.toggle("hidden", !cliHasUp);
  document.getElementById("dot-plus").classList.toggle("hidden", !plusHasUp);
  document.getElementById("dot-portable").classList.toggle("hidden", !portHasUp);
  document.getElementById("dot-package").classList.toggle("hidden", !pkgHasUp);

  const pkgInput = document.getElementById("add-package-input");
  const pkgInputValue = (pkgInput && document.activeElement === pkgInput) ? pkgInput.value : null;
  const dirCliInput = document.getElementById("dir-cli");
  const dirCliValue = (dirCliInput && document.activeElement === dirCliInput) ? dirCliInput.value : null;
  const dirPlusInput = document.getElementById("dir-plus");
  const dirPlusValue = (dirPlusInput && document.activeElement === dirPlusInput) ? dirPlusInput.value : null;
  const activeRepoEl = (document.activeElement && document.activeElement.dataset && document.activeElement.dataset.rowRepo) ? document.activeElement : null;
  const activeRepoInfo = activeRepoEl ? {
    mode: activeRepoEl.dataset.rowRepo,
    id: activeRepoEl.dataset.id,
    value: activeRepoEl.value,
    selectionStart: activeRepoEl.selectionStart,
    selectionEnd: activeRepoEl.selectionEnd,
  } : null;

  document.getElementById("panel-cli").innerHTML = projectCard("cli");
  document.getElementById("panel-plus").innerHTML = projectCard("plus");
  document.getElementById("panel-portable").innerHTML = portablePanel();
  document.getElementById("panel-package").innerHTML = packagePanel();

  if (pkgInputValue !== null) {
    const el = document.getElementById("add-package-input");
    if (el) el.value = pkgInputValue;
  }
  if (dirCliValue !== null) {
    const el = document.getElementById("dir-cli");
    if (el) el.value = dirCliValue;
  }
  if (dirPlusValue !== null) {
    const el = document.getElementById("dir-plus");
    if (el) el.value = dirPlusValue;
  }
  if (activeRepoInfo) {
    const el = document.getElementById(`repo-${activeRepoInfo.mode}-${activeRepoInfo.id}`)
      || (typeof document.querySelector === "function" ? document.querySelector(`input[data-row-repo="${activeRepoInfo.mode}"][data-id="${activeRepoInfo.id}"]`) : null);
    if (el) {
      el.value = activeRepoInfo.value;
      if (typeof el.focus === "function") el.focus();
      if (typeof activeRepoInfo.selectionStart === "number" && typeof activeRepoInfo.selectionEnd === "number" && typeof el.setSelectionRange === "function") {
        try {
          el.setSelectionRange(activeRepoInfo.selectionStart, activeRepoInfo.selectionEnd);
        } catch (_) {}
      }
    }
  }

  document.getElementById("proxy-enabled").checked = pEnabled;
  document.getElementById("proxy-url").value = state.proxy || "";
  document.getElementById("download-dir").value = state.download_directory || "";
  document.getElementById("download-workers").value = state.download_workers || 8;
  document.getElementById("prerelease-enabled").checked = Boolean(state.prerelease);
  if (activeTab === "package") preloadPackageOptions();
  document.getElementById("manager-current-ver").textContent = `管理器版本: v${state.version || '1.5.16'}`;

  const curWorkers = state.download_workers || 8;
  document.querySelectorAll(".quick-pill").forEach(pill => {
    pill.classList.toggle("active", parseInt(pill.dataset.workers, 10) === curWorkers);
  });
  syncBusyButtons();
}

async function refresh() {
  const generation = ++refreshGeneration;
  try {
    const res = await api("/api/state");
    if (generation !== refreshGeneration) return;
    state = res;
    render();
  } catch (err) {
    console.error("刷新状态失败:", err);
    logLine("加载状态失败：" + (err.message || String(err)));
  }
}

async function triggerAutoRefresh(force = false) {
  const now = Date.now();
  if (!force && now - lastAutoRefreshTime < 2500) return;
  if (refreshInProgress) return;
  const cancelBtn = document.getElementById("cancel");
  if (cancelBtn && !cancelBtn.classList.contains("hidden")) return;

  refreshInProgress = true;
  lastAutoRefreshTime = now;
  try {
    await refresh();
  } catch (err) {
    console.error("自动刷新失败:", err);
  } finally {
    refreshInProgress = false;
  }
}

function startPostInstallWatcher() {
  if (postInstallTimer) clearInterval(postInstallTimer);
  postInstallCount = 0;
  postInstallTimer = setInterval(async () => {
    postInstallCount++;
    if (postInstallCount > 30) {
      clearInterval(postInstallTimer);
      postInstallTimer = null;
      return;
    }
    await triggerAutoRefresh(true);
  }, 3000);
}

function applyAdminKey(result) {
  const item = state && state.projects && state.projects.plus;
  if (!item || result.directory !== item.directory) return false;
  const directoryInput = document.getElementById("dir-plus");
  if (directoryInput && directoryInput.value.trim() !== item.directory.trim()) return false;
  item.admin_key = result.admin_key || "";
  item.admin_key_note = result.admin_key_note || "";
  const input = document.getElementById("plus-admin-key");
  const copy = document.getElementById("plus-copy-key");
  const note = document.getElementById("plus-key-note");
  if (input) input.value = item.admin_key;
  if (copy) { copy.dataset.copyKey = item.admin_key; setButtonDisabled(copy, !item.admin_key); }
  if (note) note.textContent = item.admin_key_note;
  return true;
}

async function refreshAdminKey(manual = false) {
  if (!state || !state.projects.plus || adminKeyRefreshing || adminKeySaving) return;
  const directory = state.projects.plus.directory;
  const generation = adminKeyGeneration;
  adminKeyRefreshing = true;
  try {
    const result = await api("/api/project/plus/read-key");
    if (generation !== adminKeyGeneration) return;
    if (applyAdminKey(result) && manual) {
      logLine(result.admin_key ? "管理员 Key 已重新读取。" : result.admin_key_note);
      if (!result.admin_key) await showMessage(result.admin_key_note || "未找到明文 Key，请录入已有 Key；不会自动重置凭据。");
    }
  } catch (error) {
    if (generation !== adminKeyGeneration) return;
    applyAdminKey({directory, admin_key: "", admin_key_note: "读取 Key 失败：" + error.message});
    if (manual) showMessage("读取 Key 失败：" + error.message);
  } finally {
    adminKeyRefreshing = false;
  }
}

async function poll(jobId) {
  const container = document.getElementById("progress-container");
  const bar = document.getElementById("progress-bar");
  const statusEl = document.getElementById("status-text");
  const cancelBtn = document.getElementById("cancel");

  container.classList.remove("hidden");
  cancelBtn.classList.remove("hidden");
  bar.style.width = "0%";
  statusEl.textContent = "正在处理…";
  let displayedLines = 0;

  while (true) {
    await new Promise(r => setTimeout(r, 400));
    try {
      const job = await api(`/api/job/${jobId}`);
      if (job.progress !== undefined) {
        bar.style.width = `${job.progress}%`;
      }
      if (job.lines && job.lines.length) {
        const lastLine = job.lines[job.lines.length - 1];
        statusEl.textContent = lastLine;
        job.lines.slice(displayedLines).forEach(logLine);
        displayedLines = job.lines.length;
      }
      if (job.done) {
        if (job.error) {
          statusEl.textContent = "任务失败：" + job.error;
          showMessage(job.error);
        } else {
          bar.style.width = "100%";
        }
        cancelBtn.classList.add("hidden");
        if (!job.error) setTimeout(() => container.classList.add("hidden"), 1800);
        await refresh();
        if (!job.error && (lastJobAction === "install" || activeTab === "package")) {
          startPostInstallWatcher();
        }
        break;
      }
    } catch (err) {
      logLine("读取任务失败：" + (err.message || String(err)));
      statusEl.textContent = "读取任务失败：" + err.message;
      cancelBtn.classList.add("hidden");
      break;
    }
  }
}

async function openLibrary(mode) {
  libraryTargetMode = mode;
  librarySelectedRepo = null;
  const modal = document.getElementById("library-modal");
  modal.classList.remove("hidden");
  setButtonDisabled(document.getElementById("library-add-selected"), true);
  setButtonDisabled(document.getElementById("library-open-page"), true);

  const select = document.getElementById("library-category-select");
  select.innerHTML = '<option value="全部">全部</option>';
  if (state.categories) {
    state.categories.forEach(cat => {
      if (cat !== "全部") {
        select.innerHTML += `<option value="${esc(cat)}">${esc(cat)}</option>`;
      }
    });
  }

  await queryLibrary();
}

async function queryLibrary() {
  const queryId = ++libraryQueryId;
  const cat = document.getElementById("library-category-select").value;
  const q = document.getElementById("library-search-input").value.trim();
  const searchGh = document.getElementById("library-search-github").checked;
  const tbody = document.getElementById("library-table-body");
  librarySelectedRepo = null;
  setButtonDisabled(document.getElementById("library-add-selected"), true);
  setButtonDisabled(document.getElementById("library-open-page"), true);
  document.getElementById("library-count-text").textContent = "正在查询…";
  tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 20px;">正在加载软件库…</td></tr>';

  try {
    const res = await api("/api/catalog", {category: cat, query: q, search_github: searchGh});
    if (queryId !== libraryQueryId) return;
    const items = res.items || [];
    document.getElementById("library-count-text").textContent = `显示 ${items.length} / ${res.total ?? items.length} 款软件；已添加的软件不会重复添加。`;

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 20px;">未找到匹配的软件。</td></tr>';
      return;
    }

    const currentRows = state[libraryTargetMode === "portable" ? "portable" : "packages"] || [];
    const isListed = repo => currentRows.some(r => String(r.repository).toLowerCase() === String(repo).toLowerCase());

    tbody.innerHTML = items.map(item => {
      const added = isListed(item.repository);
      const btn = added
        ? '<button type="button" class="btn-default btn-xs" disabled>已添加</button>'
        : `<button type="button" class="btn-primary btn-xs" data-lib-add-repo="${esc(item.repository)}" data-lib-add-name="${esc(item.name)}">添加</button>`;

      return `
        <tr data-lib-repo="${esc(item.repository)}" data-lib-name="${esc(item.name)}">
          <td><strong>${esc(item.name)}</strong></td>
          <td>${esc(item.description)}</td>
          <td><a href="${esc(item.repository)}" target="_blank" class="link-text">${esc(item.repository)}</a></td>
          <td style="text-align: center;">${btn}</td>
        </tr>
      `;
    }).join("");
    syncBusyButtons();
  } catch (err) {
    if (queryId !== libraryQueryId) return;
    document.getElementById("library-count-text").textContent = "查询失败，请检查提示后重试。";
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--danger); padding: 20px;">加载出错: ${esc(err.message || String(err))}</td></tr>`;
  }
}

async function openAssociateModal(recordId) {
  associateTargetId = recordId;
  const row = (state.packages || []).find(r => r.id === recordId);
  if (!row) return;

  document.getElementById("associate-modal-title").textContent = `关联已安装软件 — ${softwareDisplayName(row)}`;
  document.getElementById("associate-search-input").value = "";
  setButtonDisabled(document.getElementById("associate-confirm-btn"), true);
  document.getElementById("associate-modal").classList.remove("hidden");

  const tbody = document.getElementById("associate-table-body");
  tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary); padding: 20px;">正在扫描本机已安装软件…</td></tr>';

  try {
    const res = await api("/api/installed-apps");
    installedAppsCache = res.apps || [];
    renderAssociateList("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--danger); padding: 20px;">扫描出错: ${esc(err.message)}</td></tr>`;
  }
}

function renderAssociateList(query) {
  const tbody = document.getElementById("associate-table-body");
  const q = (query || "").toLowerCase();
  const filtered = installedAppsCache.filter(app => {
    if (!q) return true;
    return app.name.toLowerCase().includes(q) || app.directory.toLowerCase().includes(q);
  });

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary); padding: 20px;">未找到匹配的本机已安装软件。</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(app => `
    <tr data-app-id="${esc(app.id)}" data-app-name="${esc(app.name)}" data-app-ver="${esc(app.version)}" data-app-dir="${esc(app.directory)}">
      <td><strong>${esc(app.name)}</strong></td>
      <td>${esc(app.version || "未知")}</td>
      <td style="color: var(--text-secondary);">${esc(app.directory)}</td>
    </tr>
  `).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".main-tabs button").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".main-tabs button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeTab = btn.dataset.tab;
      document.getElementById("panel-cli").classList.toggle("hidden", activeTab !== "cli");
      document.getElementById("panel-plus").classList.toggle("hidden", activeTab !== "plus");
      document.getElementById("panel-portable").classList.toggle("hidden", activeTab !== "portable");
      document.getElementById("panel-package").classList.toggle("hidden", activeTab !== "package");
      if (activeTab === "plus" && usesNativeBridge) refreshAdminKey();
      if (activeTab === "package") triggerAutoRefresh(true);
      if (activeTab === "cli" && (state?.projects?.cli?.port_info || state?.projects?.cli?.is_running)) {
        triggerAutoRefresh(true);
      }
    });
  });

  document.getElementById("settings-toggle").addEventListener("click", () => {
    document.getElementById("settings-modal").classList.remove("hidden");
  });
  document.getElementById("settings-close-x").addEventListener("click", () => {
    document.getElementById("settings-modal").classList.add("hidden");
  });
  document.getElementById("settings-done-btn").addEventListener("click", () => {
    document.getElementById("settings-modal").classList.add("hidden");
  });

  const autoSaveSettings = async () => {
    let workers = parseInt(document.getElementById("download-workers").value, 10);
    if (isNaN(workers) || workers < 1) workers = 1;
    if (workers > 16) workers = 16;
    document.getElementById("download-workers").value = workers;

    try {
      await api("/api/settings", {
        proxy_enabled: document.getElementById("proxy-enabled").checked,
        proxy: document.getElementById("proxy-url").value.trim(),
        download_directory: document.getElementById("download-dir").value.trim(),
        download_workers: workers,
        prerelease: document.getElementById("prerelease-enabled").checked
      });
      const newPre = document.getElementById("prerelease-enabled").checked;
      if (state && state.prerelease !== newPre) {
        Object.keys(inlineReleases).forEach(k => delete inlineReleases[k]);
        Object.keys(inlineAssets).forEach(k => delete inlineAssets[k]);
      }
      const ind = document.getElementById("auto-save-indicator");
      ind.textContent = "已自动保存";
      ind.style.opacity = "1";
      await refresh();
    } catch (err) {
      document.getElementById("auto-save-indicator").textContent = "保存失败";
      logLine("保存设置失败：" + (err.message || String(err)));
    }
  };

  document.getElementById("proxy-enabled").addEventListener("change", autoSaveSettings);
  document.getElementById("proxy-url").addEventListener("change", autoSaveSettings);
  document.getElementById("prerelease-enabled").addEventListener("change", autoSaveSettings);
  document.getElementById("download-dir").addEventListener("change", autoSaveSettings);

  onBusyClick(document.getElementById("workers-minus"), () => {
    let val = parseInt(document.getElementById("download-workers").value, 10) || 8;
    if (val > 1) val--;
    document.getElementById("download-workers").value = val;
    return autoSaveSettings();
  });
  onBusyClick(document.getElementById("workers-plus"), () => {
    let val = parseInt(document.getElementById("download-workers").value, 10) || 8;
    if (val < 16) val++;
    document.getElementById("download-workers").value = val;
    return autoSaveSettings();
  });
  document.getElementById("download-workers").addEventListener("change", autoSaveSettings);

  document.querySelectorAll(".quick-pill").forEach(pill => {
    onBusyClick(pill, () => {
      document.getElementById("download-workers").value = pill.dataset.workers;
      return autoSaveSettings();
    });
  });

  onBusyClick(document.getElementById("browse-download"), async () => {
    try {
      const res = await api("/api/choose-directory");
      if (res.path) {
        document.getElementById("download-dir").value = res.path;
        await autoSaveSettings();
      }
    } catch (error) { showMessage(error.message); }
  });

  onBusyClick(document.getElementById("check-manager-update"), async () => {
    const msgEl = document.getElementById("manager-update-msg");
    const upBtn = document.getElementById("upgrade-manager-btn");
    msgEl.textContent = "正在检查更新…";
    try {
      const res = await api("/api/manager/check-update", {force: true});
      if (res.has_update) {
        msgEl.innerHTML = `<span style="color: var(--danger); font-weight: 600;">发现新版 v${esc(res.latest)}</span>`;
        upBtn.disabled = false;
        upBtn.onclick = async () => {
          await openUrl(res.url);
          logLine("已在系统默认浏览器中打开下载更新页面。");
        };
      } else {
        msgEl.textContent = `已是最新版 (v${esc(res.current)})`;
        upBtn.disabled = true;
      }
    } catch (err) {
      msgEl.textContent = "检查出错: " + err.message;
    }
  });

  document.getElementById("library-close-x").addEventListener("click", () => {
    document.getElementById("library-modal").classList.add("hidden");
  });
  document.getElementById("library-close-btn").addEventListener("click", () => {
    document.getElementById("library-modal").classList.add("hidden");
  });
  document.getElementById("library-category-select").addEventListener("change", queryLibrary);
  onBusyClick(document.getElementById("library-search-btn"), queryLibrary);
  document.getElementById("library-search-input").addEventListener("keydown", e => {
    if (e.key === "Enter") queryLibrary();
  });

  document.getElementById("associate-close-x").addEventListener("click", () => {
    document.getElementById("associate-modal").classList.add("hidden");
  });
  document.getElementById("associate-cancel-btn").addEventListener("click", () => {
    document.getElementById("associate-modal").classList.add("hidden");
  });
  document.getElementById("associate-search-input").addEventListener("input", e => {
    renderAssociateList(e.target.value);
  });

  onBusyClick(document.getElementById("cancel"), async () => {
    try { await api("/api/cancel"); } catch (err) { showMessage(err.message); }
  });

  const docModal = document.getElementById("doctor-modal");
  onBusyClick(document.getElementById("mac-doctor-btn"), async () => {
    docModal.classList.remove("hidden");
    const content = document.getElementById("doctor-modal-content");
    content.innerHTML = "<p style='color: var(--text-secondary); text-align: center; margin: 20px 0;'>正在诊断 Mac 环境…</p>";
    try {
      const doc = await api("/api/macos/doctor");
      content.innerHTML = `
        <div style="display: grid; grid-template-columns: 140px 1fr; row-gap: 8px; font-size: 13px;">
          <strong>操作系统：</strong> <span>macOS ${esc(doc.mac_version)}</span>
          <strong>芯片架构：</strong> <span>${doc.is_apple_silicon ? "Apple Silicon (arm64)" : "Intel (x86_64)"}</span>
          <strong>磁盘空闲空间：</strong> <span>${doc.free_disk_gb} GB</span>
          <strong>端口 8317：</strong> <span>${doc.ports["8317"].status || "已被 " + doc.ports["8317"].command + " 占用"}</span>
          <strong>端口 18317：</strong> <span>${doc.ports["18317"].status || "已被 " + doc.ports["18317"].command + " 占用"}</span>
          <strong>PATH 路径：</strong> <span>${doc.has_local_bin_in_path ? "已包含 ~/.local/bin" : "未包含"}</span>
        </div>
      `;
    } catch (e) { content.innerHTML = "<p style='color: var(--danger);'>诊断失败：" + esc(e.message) + "</p>"; }
  });
  document.getElementById("doctor-modal-close").addEventListener("click", () => docModal.classList.add("hidden"));
  document.getElementById("doctor-done-btn").addEventListener("click", () => docModal.classList.add("hidden"));

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("focus", () => {
      if (activeTab === "package") {
        triggerAutoRefresh();
      } else if (activeTab === "cli" || activeTab === "plus") {
        triggerAutoRefresh(true);
      }
      if (activeTab === "plus" && usesNativeBridge) refreshAdminKey();
    });
  }
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        if (activeTab === "package") {
          triggerAutoRefresh();
        } else if (activeTab === "cli" || activeTab === "plus") {
          triggerAutoRefresh(true);
        }
        if (activeTab === "plus" && usesNativeBridge) refreshAdminKey();
      }
    });
  }
});

async function handleClick(event) {
  const target = event.target.closest?.("button, a") || event.target;
  return await withBusyButton(target, () => performClick(event));
}

async function performClick(event) {
  const target = event.target.closest?.("button, a") || event.target;
  if (target.dataset.project) {
    const key = target.dataset.project;
    const action = target.dataset.action;
    if (pendingServiceActions[key]) return;
    if (key === "cli" && state.projects?.cli?.brew_missing && action !== "check") {
      showMessage("未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：https://brew.sh");
      return;
    }
    const cliItem = state.projects?.cli;
    const hasPortConflict = cliItem && (cliItem.port_info || (cliItem.service && cliItem.service.includes("占用")));
    if (key === "cli" && (action === "start" || action === "restart") && hasPortConflict) {
      const p = cliItem.port_info || {};
      const loc = p.path ? `，路径：${p.path}` : (p.command ? `，进程：${p.command}` : "");
      const msg = `端口 ${p.port || 8317} 已被其他程序占用（PID ${p.pid || ""}${loc}），无法启动 Homebrew 服务。请先停止占用该端口的程序。`;
      showMessage(msg);
      logLine("[错误] " + msg);
      return;
    }
    if (action === "clear_backups") {
      if (!await confirmAction("确定清空当前服务的历史安装备份？清空后无法回滚旧版本。")) return;
      try {
        const res = await api(`/api/project/${key}/clear-backups`);
        await refresh();
        showMessage(`已清理 ${res.deleted || 0} 个历史安装备份。`);
      } catch (err) { showMessage(err.message); }
      return;
    }
    const isServiceAction = ["start", "stop", "restart"].includes(action);
    if (isServiceAction) {
      pendingServiceActions[key] = action;
      ++refreshGeneration;
      render();
    }
    try {
      const data = await api(`/api/project/${key}/${action}`, {force: action === "check"});
      if (data && data.job_id) await poll(data.job_id);
      else await refresh();
    } catch (err) {
      showMessage(err.message);
      if (isServiceAction) await refresh();
    } finally {
      if (isServiceAction) {
        delete pendingServiceActions[key];
        render();
      }
    }
    return;
  }

  if (target.dataset.openPage) {
    const key = target.dataset.openPage;
    if (usesNativeBridge) {
      try {
        await invokeNative("open_project_page", {key});
        logLine("已在系统默认浏览器中打开后台。");
      } catch (error) { showMessage("打开后台失败：" + error.message); }
      return;
    }
    const endpoint = (state.projects[key] || {}).endpoint;
    if (!endpoint) return showMessage("服务尚未启动，请先启动服务。");
    window.open(endpoint, "_blank");
    return;
  }

  if (target.dataset.copyKey !== undefined) {
    const keyVal = target.dataset.copyKey;
    if (!keyVal) return showMessage("未找到明文 Key，请重新读取或录入已有 Key；重启已有服务不会重新生成。");
    try {
      if (usesNativeBridge) await invokeNative("copy_text", {text: keyVal});
      else await navigator.clipboard.writeText(keyVal);
      showMessage("管理员登录 Key 已成功复制到剪贴板！");
    } catch (error) {
      if (usesNativeBridge) return showMessage("复制 Key 失败：" + error.message);
      const input = document.getElementById("plus-admin-key");
      if (input) {
        input.select();
        if (document.execCommand("copy")) showMessage("登录 Key 已复制！");
        else showMessage("自动复制失败，请选中 Key 后按 ⌘C。");
      } else {
        showMessage("未找到可复制的 Key。");
      }
    }
    return;
  }
  if (target.dataset.refreshKey) {
    await refreshAdminKey(true);
    return;
  }
  if (target.dataset.enterKey) {
    if (adminKeySaving) return;
    const val = await promptText("请输入已有管理员登录 Key（仅保存副本，不修改或重置服务凭据）：", true);
    if (val && val.trim()) {
      adminKeySaving = true;
      adminKeyGeneration += 1;
      try {
        const result = await api(`/api/project/plus/save-key`, {key: val.trim()});
        if (usesNativeBridge) applyAdminKey(result);
        else await refresh();
        showMessage("管理员 Key 保存成功！");
      } catch (err) { showMessage(err.message); }
      finally { adminKeySaving = false; }
    }
    return;
  }

  if (target.dataset.resetKey) {
    if (!await confirmAction("确定要重置 CPA-Manager-Plus 管理员登录 Key 吗？将执行命令重置并生成新的管理员密码。")) return;
    try {
      const result = await api(`/api/project/plus/reset-key`);
      if (usesNativeBridge) applyAdminKey(result);
      else await refresh();
      showMessage("管理员 Key 重置成功！新 Key 已自动保存并填入。");
      logLine("管理员 Key 重置成功。");
    } catch (err) { showMessage("重置管理员 Key 失败：" + err.message); }
    return;
  }

  if (target.dataset.browse) {
    const key = target.dataset.browse;
    const res = await api("/api/choose-directory");
    if (res.path) {
      document.getElementById("dir-" + key).value = res.path;
      state = await api(`/api/project/${key}/directory`, {directory: res.path});
      render();
    }
    return;
  }

  if (target.dataset.openLibrary) {
    await openLibrary(target.dataset.openLibrary);
    return;
  }

  if (target.dataset.libAddRepo) {
    const repo = target.dataset.libAddRepo;
    const name = target.dataset.libAddName;
    try {
      const dir = libraryTargetMode === "portable" ? "~/Applications/" + name : "";
      state = await api(`/api/software/${libraryTargetMode}/add`, {
        name,
        repository: repo,
        directory: dir,
        preserve: ""
      });
      render();
      target.textContent = "已添加";
      setButtonDisabled(target, true);
    } catch (err) { showMessage(err.message); }
    return;
  }

  const libTr = target.closest("tr[data-lib-repo]");
  if (libTr) {
    document.querySelectorAll("#library-table-body tr").forEach(tr => tr.classList.remove("selected"));
    libTr.classList.add("selected");
    librarySelectedRepo = {
      repo: libTr.dataset.libRepo,
      name: libTr.dataset.libName
    };
    const records = state[libraryTargetMode === "portable" ? "portable" : "packages"] || [];
    setButtonDisabled(document.getElementById("library-add-selected"), records.some(record =>
      record.repository.toLowerCase().replace(/\/$/, "") === librarySelectedRepo.repo.toLowerCase().replace(/\/$/, "")));
    setButtonDisabled(document.getElementById("library-open-page"), false);
    return;
  }

  if (target.id === "library-add-selected" && librarySelectedRepo) {
    try {
      const dir = libraryTargetMode === "portable" ? "~/Applications/" + librarySelectedRepo.name : "";
      state = await api(`/api/software/${libraryTargetMode}/add`, {
        name: librarySelectedRepo.name,
        repository: librarySelectedRepo.repo,
        directory: dir,
        preserve: ""
      });
      render();
      showMessage(`已成功添加 ${librarySelectedRepo.name} 到软件列表！`);
      document.getElementById("library-modal").classList.add("hidden");
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.id === "library-open-page" && librarySelectedRepo) {
    openUrl(librarySelectedRepo.repo);
    return;
  }

  if (target.dataset.associateApp) {
    await openAssociateModal(target.dataset.associateApp);
    return;
  }

  const appTr = target.closest("tr[data-app-id]");
  if (appTr) {
    document.querySelectorAll("#associate-table-body tr").forEach(tr => tr.classList.remove("selected"));
    appTr.classList.add("selected");
    const confirmBtn = document.getElementById("associate-confirm-btn");
    setButtonDisabled(confirmBtn, false);
    confirmBtn.dataset.appId = appTr.dataset.appId;
    confirmBtn.dataset.appName = appTr.dataset.appName;
    confirmBtn.dataset.appVer = appTr.dataset.appVer;
    confirmBtn.dataset.appDir = appTr.dataset.appDir;
    return;
  }

  if (target.id === "associate-confirm-btn") {
    if (!associateTargetId) return;
    try {
      await api("/api/software/package/bind-installed", {
        id: associateTargetId,
        app_id: target.dataset.appId,
        app_name: target.dataset.appName,
        app_version: target.dataset.appVer,
        app_dir: target.dataset.appDir
      });
      await refresh();
      document.getElementById("associate-modal").classList.add("hidden");
      logLine(`[关联已安装软件] 成功关联：${target.dataset.appName} (版本 ${target.dataset.appVer})`);
    } catch (err) { showMessage(err.message); }
    return;
  }

  if (target.id === "associate-unbind-btn") {
    if (!associateTargetId) return;
    try {
      await api("/api/software/package/unbind-installed", {id: associateTargetId});
      await refresh();
      document.getElementById("associate-modal").classList.add("hidden");
      logLine("[关联已安装软件] 已取消关联。");
    } catch (err) { showMessage(err.message); }
    return;
  }

  if (target.dataset.rowCheck || target.dataset.rowInstall) {
    const rowMode = target.dataset.rowCheck || target.dataset.rowInstall;
    const rowAction = target.dataset.rowCheck ? "check" : "install";
    lastJobAction = rowAction;
    const id = target.dataset.id;
    if (rowAction === "check") {
      delete inlineReleases[id];
      delete inlineAssets[id];
      const row = (state[rowMode === "portable" ? "portable" : "packages"] || []).find(p => p.id === id);
      if (row) {
        row.selected_tag = "";
        row.selected_asset = "";
      }
      const vSelect = document.getElementById("pkg-version-" + id);
      if (vSelect) vSelect.value = "";
      const aSelect = document.getElementById("pkg-asset-" + id);
      if (aSelect) aSelect.value = "";
    }
    try {
      const data = await api(`/api/software/${rowMode}/${rowAction}`, {id, force: rowAction === "check"});
      if (data && data.job_id) await poll(data.job_id);
      else await refresh();
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.dataset.rowUninstall) {
    const id = target.dataset.id;
    if (!await confirmAction("确定将已关联的应用移至废纸篓？个人配置不会删除，请先正常退出该应用。")) return;
    try {
      state = await api(`/api/software/package/uninstall`, {id});
      render();
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.dataset.rowOpen) {
    await api("/api/open", {path: target.dataset.dir});
    return;
  }
  if (target.dataset.rowClearBackup) {
    const id = target.dataset.id;
    if (!await confirmAction("确定清空当前软件的历史安装备份？")) return;
    try {
      const res = await api(`/api/software/portable/clear-backups`, {id});
      showMessage(`已清理 ${res.deleted || 0} 个历史安装备份。`);
    } catch (err) { showMessage(err.message); }
    return;
  }

  if (target.dataset.checkAll) {
    const mode = target.dataset.checkAll;
    lastJobAction = "check";
    Object.keys(inlineReleases).forEach(k => delete inlineReleases[k]);
    Object.keys(inlineAssets).forEach(k => delete inlineAssets[k]);
    (state[mode === "portable" ? "portable" : "packages"] || []).forEach(row => {
      row.selected_tag = "";
      row.selected_asset = "";
      const vSelect = document.getElementById("pkg-version-" + row.id);
      if (vSelect) vSelect.value = "";
      const aSelect = document.getElementById("pkg-asset-" + row.id);
      if (aSelect) aSelect.value = "";
    });
    try {
      const data = await api(`/api/software/${mode}/check`, {all: true, force: true});
      if (data && data.job_id) await poll(data.job_id);
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.dataset.upgradeAll) {
    const mode = target.dataset.upgradeAll;
    lastJobAction = "install";
    try {
      const data = await api(`/api/software/${mode}/upgrade-all`, {});
      if (data && data.job_id) await poll(data.job_id);
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.dataset.remove) {
    const mode = target.dataset.remove;
    const id = selected[mode];
    if (!id) return showMessage("请先在列表中选中一行。");
    if (!await confirmAction("确定从列表中移出该配置记录？")) return;
    state = await api(`/api/software/${mode}/remove`, {id});
    selected[mode] = "";
    render();
    return;
  }
  if (target.id === "refresh-installed-btn") {
    logLine("正在扫描 macOS 已安装软件记录…");
    try {
      const res = await api("/api/installed-apps");
      logLine(`macOS 已安装软件扫描完成，已加载 ${res.apps.length} 条记录。`);
      await refresh();
    } catch (err) {
      logLine("扫描已安装软件出错: " + err.message);
    }
    return;
  }
  if (target.dataset.openDownloadDir !== undefined) {
    await api("/api/open", {path: state.download_directory});
    return;
  }
  if (target.dataset.cleanAllDownloads !== undefined) {
    if (!await confirmAction("确定清理管理器下载且未被修改的安装包？其他下载文件不会删除。")) return;
    const result = await api("/api/software/package/clean-downloads");
    logLine(`已清理 ${result.deleted || 0} 个管理器下载包。`);
    return;
  }

  if (target.id === "btn-portable-fetch-asset") {
    const id = selected.portable;
    if (!id) return showMessage("请先选择免安装软件。");
    const result = await api("/api/software/portable/check", {id, force: true});
    if (result.job_id) await poll(result.job_id);
    else await refresh();
    return;
  }

  if (target.id === "btn-portable-add-dir") {
    const res = await api("/api/choose-directory");
    if (res.path) {
      const dirName = res.path.split("/").pop() || "software";
      const repo = await promptText(`请为解压目录 [${res.path}] 输入对应的 GitHub 仓库地址 (例如 https://github.com/作者/仓库):`);
      if (repo && repo.trim()) {
        try {
          state = await api("/api/software/portable/add", {
            name: dirName,
            repository: repo.trim(),
            directory: res.path,
            preserve: ""
          });
          render();
          logLine(`已添加免安装软件：${dirName}，解压目录：${res.path}`);
        } catch (err) { showMessage(err.message); }
      }
    }
    return;
  }

  if (target.id === "btn-add-package") {
    const input = document.getElementById("add-package-input");
    const repo = input.value.trim();
    if (!repo) return showMessage("请填写 GitHub 仓库地址。");
    const name = repoProjectName(repo) || "package";
    try {
      state = await api("/api/software/package/add", {
        name,
        repository: repo,
        directory: "",
        preserve: ""
      });
      input.value = "";
      render();
      logLine(`已添加安装向导软件：${name}`);
    } catch (err) { showMessage(err.message); }
    return;
  }
}

document.body.addEventListener("click", async event => {
  const link = typeof event?.target?.closest === "function" ? event.target.closest("a[href]") : null;
  if (link && typeof link.getAttribute === "function") {
    const href = (link.getAttribute("href") || "").trim();
    if (href.startsWith("http://") || href.startsWith("https://")) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      await openUrl(href);
      return;
    }
  }
  try { await handleClick(event); }
  catch (error) { showMessage(error.message || String(error)); }
});

document.body.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("button, a, input, select")) return;
  const row = target.closest("tr[data-id]");
  if (!row) return;
  const mode = row.dataset.mode;
  selected[mode] = row.dataset.id;
  render();
});

document.body.addEventListener("focusin", event => {
  const target = event.target;
  if (target.dataset?.rowVersion === "package" || target.dataset?.rowAsset === "package") {
    const id = target.dataset.id;
    const row = (state.packages || []).find(p => p.id === id);
    if (row) preloadRowOptions(id, row.repository, row.selected_tag || row.latest || "");
  }
  if (target?.dataset?.rowRepo) {
    const row = target.closest ? target.closest("tr[data-id]") : null;
    if (row && row.dataset && selected[row.dataset.mode] !== row.dataset.id) {
      selected[row.dataset.mode] = row.dataset.id;
      const tbody = row.parentElement;
      if (tbody && typeof tbody.querySelectorAll === "function") {
        tbody.querySelectorAll("tr").forEach(tr => {
          if (tr.classList) tr.classList.toggle("selected", tr === row);
        });
      }
      if (row.dataset.mode === "portable") {
        const bottomRepo = document.getElementById("portable-edit-repo");
        const bottomKeep = document.getElementById("portable-edit-keep");
        const bottomAsset = document.getElementById("portable-edit-asset");
        const cur = (state.portable || []).find(r => r.id === row.dataset.id);
        if (bottomRepo && cur) bottomRepo.value = cur.repository || "";
        if (bottomKeep && cur) bottomKeep.value = cur.preserve || "";
        if (bottomAsset && cur) bottomAsset.value = cur.asset || "";
      }
    }
  }
});

document.body.addEventListener("focusout", async event => {
  const target = event.target;
  if (target && (target.id === "dir-cli" || target.id === "dir-plus")) {
    target.setAttribute("readonly", "readonly");
  }
  if (target?.dataset?.rowRepo) {
    await handleRowRepoChange(event);
  }
});

document.body.addEventListener("keydown", event => {
  const target = event.target;
  if (target && (target.id === "dir-cli" || target.id === "dir-plus")) {
    if (event.key === "Enter") {
      event.preventDefault();
      target.setAttribute("readonly", "readonly");
      if (typeof target.blur === "function") target.blur();
    }
  }
  if (target?.dataset?.rowRepo) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (typeof target.blur === "function") target.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      const mode = target.dataset.rowRepo;
      const id = target.dataset.id;
      const list = mode === "portable" ? (state.portable || []) : (state.packages || []);
      const row = list.find(item => item.id === id);
      if (row) target.value = row.repository;
      if (typeof target.blur === "function") target.blur();
    }
  }
});

document.body.addEventListener("dblclick", event => {
  const target = event.target;
  if (target && (target.id === "dir-cli" || target.id === "dir-plus")) {
    target.removeAttribute("readonly");
    target.focus();
    if (typeof target.select === "function") target.select();
  }
});

document.body.addEventListener("change", async event => {
  const target = event.target;
  if (target?.dataset?.rowRepo) {
    await handleRowRepoChange(event);
    return;
  }
  if (target?.dataset?.rowVersion === "package") {
    await handleInlineVersionChange(event);
    return;
  }
  if (target?.dataset?.rowAsset === "package") {
    await handleInlineAssetChange(event);
    return;
  }
  if (target.id === "release-asset" && releaseEditor) {
    document.getElementById("release-apply").disabled = !(target.value ?
      releaseEditor.assets.some(asset => asset.name === target.value) : releaseEditor.recommended);
    return;
  }
  if (target.id === "portable-edit-repo" || target.id === "portable-edit-keep") {
    if (!selected.portable) return;
    try {
      const field = target.id === "portable-edit-repo" ? "repository" : "preserve";
      state = await api("/api/software/portable/edit", {id: selected.portable, [field]: target.value});
      render();
      logLine("免安装软件配置已保存。");
    } catch (error) { showMessage(error.message); }
    return;
  }
  if (target.id === "dir-cli" || target.id === "dir-plus") {
    target.setAttribute("readonly", "readonly");
    const key = target.id.slice(4);
    try {
      state = await api(`/api/project/${key}/directory`, {directory: target.value});
      render();
      logLine(`[${key.toUpperCase()}] 解压目录已更新: ${target.value}`);
    } catch (err) { showMessage(err.message); }
  }
});

(async function init() {
  await refresh();

  if (state && state.projects) {
    if (state.projects.cli) {
      if (state.projects.cli.brew_missing) {
        logLine("[CLIProxyAPI] 管理页面就绪（未检测到 Homebrew，请先安装 brew）");
      } else {
        logLine("[CLIProxyAPI] 管理页面就绪（Homebrew 模式）");
      }
    }
    if (state.projects.plus) logLine(`[CPA-Manager-Plus] 管理页面就绪，解压目录: ${state.projects.plus.directory}`);
  }
  logLine("免安装软件管理页面就绪。");
  logLine("安装向导软件管理页面就绪。");

  if (usesNativeBridge) setInterval(() => {
    if (activeTab === "plus" && !document.hidden) {
      refreshAdminKey();
      if (state?.projects?.plus?.port_info || state?.projects?.plus?.is_running) {
        triggerAutoRefresh();
      }
    }
    if (activeTab === "package" && !document.hidden) triggerAutoRefresh();
    if (activeTab === "cli" && !document.hidden && (state?.projects?.cli?.port_info || state?.projects?.cli?.is_running)) {
      triggerAutoRefresh();
    }
  }, 3000);

  try {
    const res = await api("/api/manager/check-update", {force: false});
    if (res.has_update) {
      logLine(`[更新检查] 发现管理器新版本：v${res.latest}，可前往设置升级。`);
      const msgEl = document.getElementById("manager-update-msg");
      const upBtn = document.getElementById("upgrade-manager-btn");
      if (msgEl) msgEl.innerHTML = `<span style="color: var(--danger); font-weight: 600;">发现新版 v${esc(res.latest)}</span>`;
      if (upBtn) {
        upBtn.disabled = false;
        upBtn.onclick = async () => {
          await openUrl(res.url);
          logLine("已在系统默认浏览器中打开下载更新页面。");
        };
      }
    }
  } catch (_) {}
})();
