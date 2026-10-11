let state = null;
let activeTab = "cli";
let selected = {portable: "", package: ""};
let libraryTargetMode = "package";
let associateTargetId = null;
let installedAppsCache = [];
let librarySelectedRepo = null;
const isTauri = Boolean(window.__TAURI__ && window.__TAURI__.core);

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

async function api(path, body) {
  if (isTauri) {
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
      return await window.__TAURI__.core.invoke(cmdMap[path], body || {});
    }
    if (path.startsWith("/api/project/")) {
      const parts = path.split("/");
      return await window.__TAURI__.core.invoke("project_action", {
        key: parts[3],
        action: parts[4],
        body: body || {}
      });
    }
    if (path.startsWith("/api/software/")) {
      const parts = path.split("/");
      return await window.__TAURI__.core.invoke("software_action", {
        mode: parts[3],
        action: parts[4],
        body: body || {}
      });
    }
  }

  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {})
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
    statusHtml = `<span class="service-status-text service-status-stopped">未运行</span>`;
    buttonsHtml = `
      <button type="button" class="btn-success" data-project="${key}" data-action="start">启动</button>
      <button type="button" disabled>停止</button>
      <button type="button" disabled>重启</button>`;
  }

  let adminKeyHtml = "";
  if (key === "plus") {
    adminKeyHtml = `
      <div class="key-card">
        <h4 class="card-box-title" style="margin-bottom: 4px;">管理员登录 Key</h4>
        <div class="key-row">
          <input type="text" id="plus-admin-key" readonly value="${esc(item.admin_key || '')}" placeholder="尚未生成或捕获 Key" class="key-input">
          <button type="button" class="btn-primary" data-copy-key="${esc(item.admin_key || '')}">复制 Key</button>
          <button type="button" data-enter-key="plus">录入已有 Key</button>
        </div>
        <div class="meta-hint">首次启动自动捕获 Key；已有 Key 可录入，使用 macOS 当前用户安全保存。</div>
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

    <div class="meta-hint">首次安装自动创建本机监听配置；使用前请在 config.yaml 配置上游账号。</div>
    <div class="meta-hint">解压目录自动保存</div>

    <div class="row-flex" style="margin-top: 14px;">
      <button type="button" class="btn-default" data-project="${key}" data-action="check">检查最新版</button>
      <button type="button" class="btn-primary" data-project="${key}" data-action="install" ${upgradeDisabled}>升级</button>
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
    const nameDisplay = (hasUp ? '<span class="red-indicator">↑ </span>' : '') + esc(row.name);
    const localVer = row.installed_version || row.local || "未关联";

    return `
      <tr data-id="${esc(row.id)}" data-mode="package" class="${isSel}">
        <td><strong>${nameDisplay}</strong></td>
        <td><a href="${esc(row.repository)}" target="_blank" class="link-text">${esc(row.repository)}</a></td>
        <td><a href="javascript:void(0)" class="link-text" data-associate-app="${esc(row.id)}">${esc(localVer)}</a></td>
        <td>${esc(row.latest || "尚未检查")}</td>
        <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${esc(row.asset || "-")}</td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs" data-row-check="package" data-id="${esc(row.id)}">检查</button></td>
        <td style="text-align: center;"><button type="button" class="btn-default btn-xs ${hasUp ? 'btn-primary' : ''}" data-row-install="package" data-id="${esc(row.id)}">安装</button></td>
        <td style="text-align: center;"><button type="button" class="btn-danger btn-xs" data-row-uninstall="package" data-id="${esc(row.id)}">卸载</button></td>
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

    <div class="meta-hint">本地版本来自 macOS 已安装软件记录；点击版本或双击可关联软件、查看安装目录。双击名称或 GitHub 地址可编辑。</div>

    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 150px;">软件</th>
            <th>GitHub 地址</th>
            <th style="width: 120px;">本地安装版本</th>
            <th style="width: 110px;">选择安装版本 ▾</th>
            <th style="width: 220px;">对应包 ▾</th>
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

    <div class="meta-hint">右侧操作列固定显示，点击即可操作该行；底部横向滚动条可查看软件信息，点击对应包可选择附件。</div>
    <div class="meta-hint">填写 GitHub 地址添加软件，或选择一行检查、安装、更新。</div>
  `;
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

  document.getElementById("panel-cli").innerHTML = projectCard("cli");
  document.getElementById("panel-plus").innerHTML = projectCard("plus");
  document.getElementById("panel-portable").innerHTML = portablePanel();
  document.getElementById("panel-package").innerHTML = packagePanel();

  document.getElementById("proxy-enabled").checked = pEnabled;
  document.getElementById("proxy-url").value = state.proxy || "";
  document.getElementById("download-dir").value = state.download_directory || "";
  document.getElementById("download-workers").value = state.download_workers || 8;
  document.getElementById("prerelease-enabled").checked = Boolean(state.prerelease);
  document.getElementById("manager-current-ver").textContent = `管理器版本: v${state.version || '1.5.14'}`;

  const curWorkers = state.download_workers || 8;
  document.querySelectorAll(".quick-pill").forEach(pill => {
    pill.classList.toggle("active", parseInt(pill.dataset.workers, 10) === curWorkers);
  });
}

async function refresh() {
  try {
    const res = await fetch("/api/state").then(r => r.json());
    state = res;
    render();
  } catch (err) {
    console.error("刷新状态失败:", err);
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

  while (true) {
    await new Promise(r => setTimeout(r, 400));
    try {
      const job = await fetch(`/api/job/${jobId}`).then(r => r.json());
      if (job.progress !== undefined) {
        bar.style.width = `${job.progress}%`;
      }
      if (job.lines && job.lines.length) {
        const lastLine = job.lines[job.lines.length - 1];
        statusEl.textContent = lastLine;
        const logEl = document.getElementById("log");
        logEl.textContent = job.lines.join("\n");
        logEl.scrollTop = logEl.scrollHeight;
      }
      if (job.done) {
        bar.style.width = "100%";
        cancelBtn.classList.add("hidden");
        setTimeout(() => container.classList.add("hidden"), 1800);
        await refresh();
        break;
      }
    } catch (_) {
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
  const cat = document.getElementById("library-category-select").value;
  const q = document.getElementById("library-search-input").value.trim();
  const searchGh = document.getElementById("library-search-github").checked;
  const tbody = document.getElementById("library-table-body");
  tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 20px;">正在加载软件库…</td></tr>';

  try {
    const res = await api("/api/catalog", {category: cat, query: q, search_github: searchGh});
    const items = res.items || [];
    document.getElementById("library-count-text").textContent = `显示 ${items.length} / ${res.total || items.length} 款软件；已添加的软件不会重复添加。`;

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
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--danger); padding: 20px;">加载出错: ${esc(err.message)}</td></tr>`;
  }
}

async function openAssociateModal(recordId) {
  associateTargetId = recordId;
  const row = (state.packages || []).find(r => r.id === recordId);
  if (!row) return;

  document.getElementById("associate-modal-title").textContent = `关联已安装软件 — ${row.name}`;
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
      <td>${esc(app.version)}</td>
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
      const ind = document.getElementById("auto-save-indicator");
      ind.textContent = "已自动保存";
      ind.style.opacity = "1";
      await refresh();
    } catch (err) {
      console.error("保存失败:", err);
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
    const res = await api("/api/choose-directory");
    if (res.path) {
      document.getElementById("download-dir").value = res.path;
      autoSaveSettings();
    }
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
    try { await api("/api/cancel"); } catch (err) { alert(err.message); }
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
});

document.body.addEventListener("click", async event => {
  if (event.target.dataset.project) {
    const key = event.target.dataset.project;
    const action = event.target.dataset.action;
    if (action === "clear_backups") {
      if (!confirm("确定清空当前服务的历史安装备份？清空后无法回滚旧版本。")) return;
      try {
        const res = await api(`/api/project/${key}/clear-backups`);
        await refresh();
        alert(`已清理 ${res.deleted || 0} 个历史安装备份。`);
      } catch (err) { alert(err.message); }
      return;
    }
    try {
      const data = await api(`/api/project/${key}/${action}`, {force: action === "check"});
      if (data && data.job_id) poll(data.job_id);
      else await refresh();
    } catch (err) { alert(err.message); }
    return;
  }

  if (event.target.dataset.openPage) {
    const key = event.target.dataset.openPage;
    const endpoint = (state.projects[key] || {}).endpoint;
    if (!endpoint) return alert("服务尚未启动，请先启动服务。");
    window.open(endpoint, "_blank");
    return;
  }

  if (event.target.dataset.copyKey !== undefined) {
    const keyVal = event.target.dataset.copyKey;
    if (!keyVal) return alert("尚未捕获到 Key，请先启动服务生成。");
    try {
      await navigator.clipboard.writeText(keyVal);
      alert("管理员登录 Key 已成功复制到剪贴板！");
    } catch (_) {
      const input = document.getElementById("plus-admin-key");
      if (input) { input.select(); document.execCommand("copy"); alert("登录 Key 已复制！"); }
    }
    return;
  }
  if (event.target.dataset.enterKey) {
    const val = prompt("请输入已有管理员登录 Key：");
    if (val && val.trim()) {
      try {
        await api(`/api/project/plus/save-key`, {key: val.trim()});
        await refresh();
        alert("管理员 Key 保存成功！");
      } catch (err) { alert(err.message); }
    }
    return;
  }

  if (event.target.dataset.browse) {
    const key = event.target.dataset.browse;
    const res = await api("/api/choose-directory");
    if (res.path) {
      document.getElementById("dir-" + key).value = res.path;
      state = await api(`/api/project/${key}/directory`, {directory: res.path});
      render();
    }
    return;
  }

  if (event.target.dataset.openLibrary) {
    openLibrary(event.target.dataset.openLibrary);
    return;
  }

  if (event.target.dataset.libAddRepo) {
    const repo = event.target.dataset.libAddRepo;
    const name = event.target.dataset.libAddName;
    try {
      const dir = libraryTargetMode === "portable" ? "~/Applications/" + name : "";
      state = await api(`/api/software/${libraryTargetMode}/add`, {
        name,
        repository: repo,
        directory: dir,
        preserve: ""
      });
      render();
      event.target.textContent = "已添加";
      event.target.disabled = true;
    } catch (err) { alert(err.message); }
    return;
  }

  const libTr = event.target.closest("tr[data-lib-repo]");
  if (libTr) {
    document.querySelectorAll("#library-table-body tr").forEach(tr => tr.classList.remove("selected"));
    libTr.classList.add("selected");
    librarySelectedRepo = {
      repo: libTr.dataset.libRepo,
      name: libTr.dataset.libName
    };
    document.getElementById("library-add-selected").disabled = false;
    document.getElementById("library-open-page").disabled = false;
    return;
  }

  if (event.target.id === "library-add-selected" && librarySelectedRepo) {
    try {
      const dir = libraryTargetMode === "portable" ? "~/Applications/" + librarySelectedRepo.name : "";
      state = await api(`/api/software/${libraryTargetMode}/add`, {
        name: librarySelectedRepo.name,
        repository: librarySelectedRepo.repo,
        directory: dir,
        preserve: ""
      });
      render();
      alert(`已成功添加 ${librarySelectedRepo.name} 到软件列表！`);
      document.getElementById("library-modal").classList.add("hidden");
    } catch (err) { alert(err.message); }
    return;
  }
  if (event.target.id === "library-open-page" && librarySelectedRepo) {
    window.open(librarySelectedRepo.repo, "_blank");
    return;
  }

  if (event.target.dataset.associateApp) {
    openAssociateModal(event.target.dataset.associateApp);
    return;
  }

  const appTr = event.target.closest("tr[data-app-id]");
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

  if (event.target.id === "associate-confirm-btn") {
    if (!associateTargetId) return;
    try {
      await api("/api/software/package/bind-installed", {
        id: associateTargetId,
        app_id: event.target.dataset.appId,
        app_name: event.target.dataset.appName,
        app_version: event.target.dataset.appVer,
        app_dir: event.target.dataset.appDir
      });
      await refresh();
      document.getElementById("associate-modal").classList.add("hidden");
      logLine(`[关联已安装软件] 成功关联：${event.target.dataset.appName} (版本 ${event.target.dataset.appVer})`);
    } catch (err) { alert(err.message); }
    return;
  }

  if (event.target.id === "associate-unbind-btn") {
    if (!associateTargetId) return;
    try {
      await api("/api/software/package/unbind-installed", {id: associateTargetId});
      await refresh();
      document.getElementById("associate-modal").classList.add("hidden");
      logLine("[关联已安装软件] 已取消关联。");
    } catch (err) { alert(err.message); }
    return;
  }

  if (event.target.dataset.rowCheck || event.target.dataset.rowInstall) {
    const rowMode = event.target.dataset.rowCheck ? "portable" : "package";
    const rowAction = event.target.dataset.rowCheck ? "check" : "install";
    const id = event.target.dataset.id;
    try {
      const data = await api(`/api/software/${rowMode}/${rowAction}`, {id, force: rowAction === "check"});
      if (data && data.job_id) poll(data.job_id);
    } catch (err) { alert(err.message); }
    return;
  }
  if (event.target.dataset.rowUninstall) {
    const id = event.target.dataset.id;
    if (!confirm("确定从列表中移除并卸载该软件？")) return;
    try {
      state = await api(`/api/software/package/uninstall`, {id});
      render();
    } catch (err) { alert(err.message); }
    return;
  }
  if (event.target.dataset.rowOpen) {
    await api("/api/open", {path: event.target.dataset.dir});
    return;
  }
  if (event.target.dataset.rowClearBackup) {
    const id = event.target.dataset.id;
    try {
      const res = await api(`/api/software/portable/clear-backups`, {id});
      alert(`已清理 ${res.deleted || 0} 个历史安装备份。`);
    } catch (err) { alert(err.message); }
    return;
  }

  if (event.target.dataset.checkAll) {
    const mode = event.target.dataset.checkAll;
    try {
      const data = await api(`/api/software/${mode}/check`, {all: true, force: true});
      if (data && data.job_id) poll(data.job_id);
    } catch (err) { alert(err.message); }
    return;
  }
  if (event.target.dataset.upgradeAll) {
    const mode = event.target.dataset.upgradeAll;
    try {
      const data = await api(`/api/software/${mode}/upgrade-all`, {});
      if (data && data.job_id) poll(data.job_id);
    } catch (err) { alert(err.message); }
    return;
  }
  if (event.target.dataset.remove) {
    const mode = event.target.dataset.remove;
    const id = selected[mode];
    if (!id) return alert("请先在列表中选中一行。");
    if (!confirm("确定从列表中移出该配置记录？")) return;
    state = await api(`/api/software/${mode}/remove`, {id});
    selected[mode] = "";
    render();
    return;
  }
  if (event.target.id === "refresh-installed-btn") {
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
  if (event.target.dataset.openDownloadDir !== undefined) {
    await api("/api/open", {path: state.download_directory});
    return;
  }
  if (event.target.dataset.cleanAllDownloads !== undefined) {
    if (!confirm("确定清空下载目录中所有历史下载包？")) return;
    logLine("已清理历史下载包缓存。");
    return;
  }

  if (event.target.id === "btn-portable-add-dir") {
    const res = await api("/api/choose-directory");
    if (res.path) {
      const dirName = res.path.split("/").pop() || "software";
      const repo = prompt(`请为解压目录 [${res.path}] 输入对应的 GitHub 仓库地址 (例如 https://github.com/作者/仓库):`);
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
        } catch (err) { alert(err.message); }
      }
    }
    return;
  }

  if (event.target.id === "btn-add-package") {
    const input = document.getElementById("add-package-input");
    const repo = input.value.trim();
    if (!repo) return alert("请填写 GitHub 仓库地址。");
    const name = repo.split("/").pop() || "package";
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
    } catch (err) { alert(err.message); }
    return;
  }
});

document.body.addEventListener("click", event => {
  if (event.target.closest("button, a, input, select")) return;
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  const mode = row.dataset.mode;
  selected[mode] = row.dataset.id;
  render();
});

document.body.addEventListener("change", async event => {
  if (event.target.id === "dir-cli" || event.target.id === "dir-plus") {
    const key = event.target.id.slice(4);
    try {
      state = await api(`/api/project/${key}/directory`, {directory: event.target.value});
      render();
      logLine(`[${key.toUpperCase()}] 解压目录已更新: ${event.target.value}`);
    } catch (err) { alert(err.message); }
  }
});

(async function init() {
  await refresh();

  if (state && state.projects) {
    if (state.projects.cli) logLine(`[CLIProxyAPI] 管理页面就绪，解压目录: ${state.projects.cli.directory}`);
    if (state.projects.plus) logLine(`[CPA-Manager-Plus] 管理页面就绪，解压目录: ${state.projects.plus.directory}`);
  }
  logLine("免安装软件管理页面就绪。");
  logLine("安装向导软件管理页面就绪。");

  try {
    const res = await api("/api/manager/check-update", {force: false});
    if (res.has_update) {
      logLine(`[更新检查] 发现管理器新版本：v${res.latest}，可前往设置升级。`);
    }
  } catch (_) {}
})();
