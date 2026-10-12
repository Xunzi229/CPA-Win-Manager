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

function showMessage(message) {
  if (!usesNativeBridge) return alert(message);
  logLine(message);
  return invokeNative("plugin:dialog|message", {message: String(message), title: "CPA Mac 管理器", buttons: "Ok"})
    .catch(error => logLine("显示提示失败：" + error.message));
}

async function confirmAction(message) {
  if (!usesNativeBridge) return confirm(message);
  return await invokeNative("plugin:dialog|message", {
    message, title: "CPA Mac 管理器", kind: "warning", buttons: "OkCancel"
  }) === "Ok";
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
      if (parts[4] === "read-key" || parts[4] === "save-key") return result;
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
  if (isRunning) {
    statusHtml = `<span class="service-status-text service-status-running">运行中 (${item.service})</span>`;
    buttonsHtml = `
      <button type="button" class="btn-success" data-open-page="${key}">打开后台</button>
      <button type="button" data-project="${key}" data-action="stop">停止</button>
      <button type="button" data-project="${key}" data-action="restart">重启</button>`;
  } else if (item.port_info && !isRunning) {
    statusHtml = `<span class="service-status-text service-status-alert">未运行 (端口 ${item.port_info.port} 被其他进程占用)</span>`;
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
        <div>${statusHtml}</div>
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
      <input type="text" id="dir-${key}" class="input-flex" value="${esc(item.directory)}">
      <button type="button" class="btn-default" data-browse="${key}">选择文件夹</button>
    </div>

    <div class="info-line">
      <span>本地版本：${esc(item.local || "尚未安装")}</span>
      <span style="margin-left: 20px;">最新版本（缓存）：${esc(item.latest || "尚未检查")}</span>
      ${hasUp ? '<span class="red-indicator">◆ 有新版本</span>' : ''}
    </div>

    <div class="service-card">
      <div>${statusHtml}</div>
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
        <td><a href="${esc(row.repository)}" target="_blank" class="link-text">${esc(row.repository)}</a></td>
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
        <td><a href="${esc(row.repository)}" target="_blank" class="link-text">${esc(row.repository)}</a></td>
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

function render() {
  if (!state) return;

  const pEnabled = Boolean(state.proxy_enabled);
  document.getElementById("proxy-badge").textContent = pEnabled ? "代理：已启用" : "代理：直连";
  document.getElementById("header-version-text").textContent = `管理器版本: v${state.version || '1.5.14'}`;

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

  document.getElementById("proxy-enabled").checked = pEnabled;
  document.getElementById("proxy-url").value = state.proxy || "";
  document.getElementById("download-dir").value = state.download_directory || "";
  document.getElementById("download-workers").value = state.download_workers || 8;
  document.getElementById("prerelease-enabled").checked = Boolean(state.prerelease);
  if (activeTab === "package") preloadPackageOptions();
  document.getElementById("manager-current-ver").textContent = `管理器版本: v${state.version || '1.5.14'}`;

  const curWorkers = state.download_workers || 8;
  document.querySelectorAll(".quick-pill").forEach(pill => {
    pill.classList.toggle("active", parseInt(pill.dataset.workers, 10) === curWorkers);
  });
}

async function refresh() {
  try {
    const res = await api("/api/state");
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
  if (copy) { copy.dataset.copyKey = item.admin_key; copy.disabled = !item.admin_key; }
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
  document.getElementById("library-add-selected").disabled = true;
  document.getElementById("library-open-page").disabled = true;

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
  document.getElementById("library-add-selected").disabled = true;
  document.getElementById("library-open-page").disabled = true;
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
  document.getElementById("associate-confirm-btn").disabled = true;
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

  document.getElementById("workers-minus").addEventListener("click", () => {
    let val = parseInt(document.getElementById("download-workers").value, 10) || 8;
    if (val > 1) val--;
    document.getElementById("download-workers").value = val;
    autoSaveSettings();
  });
  document.getElementById("workers-plus").addEventListener("click", () => {
    let val = parseInt(document.getElementById("download-workers").value, 10) || 8;
    if (val < 16) val++;
    document.getElementById("download-workers").value = val;
    autoSaveSettings();
  });
  document.getElementById("download-workers").addEventListener("change", autoSaveSettings);

  document.querySelectorAll(".quick-pill").forEach(pill => {
    pill.addEventListener("click", () => {
      document.getElementById("download-workers").value = pill.dataset.workers;
      autoSaveSettings();
    });
  });

  document.getElementById("browse-download").addEventListener("click", async () => {
    try {
      const res = await api("/api/choose-directory");
      if (res.path) {
        document.getElementById("download-dir").value = res.path;
        await autoSaveSettings();
      }
    } catch (error) { showMessage(error.message); }
  });

  document.getElementById("check-manager-update").addEventListener("click", async () => {
    const msgEl = document.getElementById("manager-update-msg");
    const upBtn = document.getElementById("upgrade-manager-btn");
    msgEl.textContent = "正在检查更新…";
    try {
      const res = await api("/api/manager/check-update", {force: true});
      if (res.has_update) {
        msgEl.innerHTML = `<span style="color: var(--danger); font-weight: 600;">发现新版 v${esc(res.latest)}</span>`;
        upBtn.disabled = false;
        upBtn.onclick = () => window.open(res.url, "_blank");
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
  document.getElementById("library-search-btn").addEventListener("click", queryLibrary);
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

  document.getElementById("cancel").addEventListener("click", async () => {
    try { await api("/api/cancel"); } catch (err) { showMessage(err.message); }
  });

  const docModal = document.getElementById("doctor-modal");
  document.getElementById("mac-doctor-btn").addEventListener("click", async () => {
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
      if (activeTab === "package") triggerAutoRefresh();
    });
  }
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && activeTab === "package") triggerAutoRefresh();
    });
  }
});

async function handleClick(event) {
  const target = event.target.closest?.("button, a") || event.target;
  if (target.disabled) return;
  if (target.dataset.project) {
    const key = target.dataset.project;
    const action = target.dataset.action;
    if (key === "cli" && state.projects?.cli?.brew_missing && action !== "check") {
      showMessage("未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：https://brew.sh");
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
    try {
      const data = await api(`/api/project/${key}/${action}`, {force: action === "check"});
      if (data && data.job_id) poll(data.job_id);
      else await refresh();
    } catch (err) { showMessage(err.message); }
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
    openLibrary(target.dataset.openLibrary);
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
      target.disabled = true;
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
    document.getElementById("library-add-selected").disabled = records.some(record =>
      record.repository.toLowerCase().replace(/\/$/, "") === librarySelectedRepo.repo.toLowerCase().replace(/\/$/, ""));
    document.getElementById("library-open-page").disabled = false;
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
    window.open(librarySelectedRepo.repo, "_blank");
    return;
  }

  if (target.dataset.associateApp) {
    openAssociateModal(target.dataset.associateApp);
    return;
  }

  const appTr = target.closest("tr[data-app-id]");
  if (appTr) {
    document.querySelectorAll("#associate-table-body tr").forEach(tr => tr.classList.remove("selected"));
    appTr.classList.add("selected");
    const confirmBtn = document.getElementById("associate-confirm-btn");
    confirmBtn.disabled = false;
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
      if (data && data.job_id) poll(data.job_id);
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
      if (data && data.job_id) poll(data.job_id);
    } catch (err) { showMessage(err.message); }
    return;
  }
  if (target.dataset.upgradeAll) {
    const mode = target.dataset.upgradeAll;
    lastJobAction = "install";
    try {
      const data = await api(`/api/software/${mode}/upgrade-all`, {});
      if (data && data.job_id) poll(data.job_id);
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
    if (result.job_id) poll(result.job_id);
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
});

document.body.addEventListener("change", async event => {
  const target = event.target;
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
    if (activeTab === "plus" && !document.hidden) refreshAdminKey();
    if (activeTab === "package" && !document.hidden) triggerAutoRefresh();
  }, 5000);

  try {
    const res = await api("/api/manager/check-update", {force: false});
    if (res.has_update) {
      logLine(`[更新检查] 发现管理器新版本：v${res.latest}，可前往设置升级。`);
    }
  } catch (_) {}
})();
