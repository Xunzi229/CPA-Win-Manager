let state = null;
let selected = {portable: "", package: ""};
let currentCategory = {portable: "全部", package: "全部"};
let searchModalMode = null;
const tabs = ["cli", "plus", "portable", "package"];

const isTauri = Boolean(window.__TAURI__ && window.__TAURI__.core);

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, body) {
  if (isTauri) {
    const cmdMap = {
      "/api/settings": "save_settings",
      "/api/cancel": "cancel_job",
      "/api/choose-directory": "choose_directory",
      "/api/open": "open_path",
      "/api/macos/doctor": "macos_doctor"
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
  const hasUp = item.update;
  const latestTag = esc(item.latest || "尚未检查") + (hasUp ? ' <span class="update-badge"><span class="red-dot"></span>可更新</span>' : "");
  const isRunning = Boolean(item.is_running);
  const statusClass = isRunning ? "status-tag active" : "status-tag";

  let mainServiceBtn = "";
  if (isRunning) {
    mainServiceBtn = `<button type="button" class="btn-success" data-open-page="${key}">打开后台</button>
      <button type="button" data-project="${key}" data-action="stop">停止</button>
      <button type="button" data-project="${key}" data-action="restart">重启</button>`;
  } else {
    mainServiceBtn = `<button type="button" class="btn-primary" data-project="${key}" data-action="start">启动</button>`;
  }

  const installBtnText = item.installed ? (hasUp ? "升级至新版" : "重新安装") : "安装最新版";
  const installBtnClass = hasUp ? "btn-primary" : "";

  let adminKeyHtml = "";
  if (key === "plus") {
    adminKeyHtml = `
      <div class="admin-key-box">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="font-weight: 600; font-size: 13.5px;">管理员登录 Key</span>
          <span style="font-size: 12px; color: var(--text-secondary);">首次启动自动捕获，或录入已有 Key</span>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <input type="text" id="plus-admin-key" readonly value="${esc(item.admin_key || '')}" placeholder="尚未生成或捕获 Key" style="flex: 1; font-family: monospace; background: var(--bg-card);">
          <button type="button" class="btn-primary" data-copy-key="${esc(item.admin_key || '')}">复制 Key</button>
          <button type="button" data-enter-key="plus">录入已有 Key</button>
        </div>
      </div>`;
  }

  const backupCount = item.backup_count || 0;
  const backupBtnText = backupCount > 0 ? `清空备份 (${backupCount})` : "清空备份";

  const launchAgent = item.launch_agent || {};
  const isLaunchAgentLoaded = Boolean(launchAgent.loaded);
  const launchAgentTag = isLaunchAgentLoaded
    ? '<span class="status-tag active" style="font-size: 11px; margin-left: 8px; background: rgba(52, 199, 89, 0.15); color: var(--success);">LaunchAgent 守护中</span>'
    : '';
  const launchAgentBtn = isLaunchAgentLoaded
    ? `<button type="button" data-project="${key}" data-action="disable_launchd" style="font-size: 12px;">停用开机常驻</button>`
    : `<button type="button" data-project="${key}" data-action="enable_launchd" style="font-size: 12px;">设为开机常驻 (LaunchAgent)</button>`;

  let portAlertHtml = "";
  if (item.port_info && !isRunning) {
    portAlertHtml = `
      <div style="margin: 8px 0; padding: 8px 12px; background: rgba(255, 59, 48, 0.12); border-left: 4px solid var(--danger); border-radius: 4px; display: flex; justify-content: space-between; align-items: center;">
        <span style="font-size: 12.5px; color: var(--danger);">⚠️ 端口 <strong>${item.port_info.port}</strong> 正在被进程 <strong>${esc(item.port_info.command)}</strong> (PID ${item.port_info.pid}) 占用</span>
        <button type="button" class="btn-danger" data-project="${key}" data-action="release_port" style="padding: 3px 8px; font-size: 11.5px;">一键释放端口</button>
      </div>`;
  }

  return `
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <div style="display: flex; align-items: center;">
        <h3 style="margin: 0; font-size: 17px;">${esc(item.title)}</h3>
        ${launchAgentTag}
      </div>
      <span class="${statusClass}">${esc(item.service)}</span>
    </div>
    ${portAlertHtml}
    <p class="meta-text" style="margin-top: 6px;">项目主页：<a href="${esc(item.repo)}" target="_blank">${esc(item.repo)}</a></p>
    <div class="row" style="margin: 12px 0;">
      <span style="font-weight: 500;">安装目录：</span>
      <input id="dir-${key}" type="text" value="${esc(item.directory)}" style="flex: 1;">
      <button type="button" data-browse="${key}">选择</button>
      <button type="button" data-open="${esc(item.directory)}">打开目录</button>
      <button type="button" data-project="${key}" data-action="open_config">打开配置</button>
      <button type="button" data-project="${key}" data-action="open_log">查看日志</button>
      <button type="button" data-project="${key}" data-action="tail_log">终端实时日志</button>
      <button type="button" data-project="${key}" data-action="terminal">终端打开</button>
      <button type="button" data-project="${key}" data-action="reveal">访达高亮</button>
    </div>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin: 12px 0; background: rgba(142, 142, 147, 0.08); padding: 12px 14px; border-radius: 8px;">
      <div><span style="color: var(--text-secondary);">本地版本：</span><strong>${esc(item.local || "未安装")}</strong></div>
      <div><span style="color: var(--text-secondary);">最新版本：</span>${latestTag}</div>
      <div><span style="color: var(--text-secondary);">附件文件：</span>${esc(item.asset || "尚未检查")}</div>
      <div><span style="color: var(--text-secondary);">管理后台：</span>${item.endpoint ? `<a href="${esc(item.endpoint)}" target="_blank">${esc(item.endpoint)}（打开页面）</a>` : "启动后可打开页面"}</div>
    </div>
    ${adminKeyHtml}
    <p class="meta-text">首次安装会写入仅监听本机的默认配置，已有配置文件不会被覆盖。</p>
    <div class="actions" style="margin-top: 14px;">
      <button type="button" data-project="${key}" data-action="check">检查最新版</button>
      <button type="button" class="${installBtnClass}" data-project="${key}" data-action="install">${installBtnText}</button>
      ${mainServiceBtn}
      ${launchAgentBtn}
      <button type="button" data-project="${key}" data-action="clear_backups" style="margin-left: auto;">${backupBtnText}</button>
    </div>`;
}

function listed(mode, repo) {
  const rows = state[mode === "portable" ? "portable" : "packages"] || [];
  return rows.some(row => String(row.repository).toLowerCase() === String(repo).toLowerCase());
}

function catalogItems(mode, query) {
  const want = mode === "portable" ? "免安装" : "安装包";
  const cat = currentCategory[mode] || "全部";
  const text = (query || "").toLowerCase();
  return (state.catalog || []).filter(item => {
    if (item.mode !== want) return false;
    if (cat !== "全部" && item.category !== cat) return false;
    if (!text) return true;
    return (item.name + " " + (item.description || "") + " " + item.repository).toLowerCase().includes(text);
  });
}

function categoryPills(mode) {
  const cats = state.categories || ["全部"];
  return `<div class="category-pills">
    ${cats.map(cat => `<button type="button" class="pill-btn ${cat === currentCategory[mode] ? 'active' : ''}" data-cat-select="${esc(cat)}" data-cat-mode="${mode}">${esc(cat)}</button>`).join("")}
  </div>`;
}

function libraryList(mode, query) {
  const items = catalogItems(mode, query);
  if (!items.length) return "<p style='color: var(--text-secondary); padding: 12px 0;'>软件库该分类下暂无匹配项。</p>";
  return items.map(item => {
    const button = listed(mode, item.repository)
      ? '<button type="button" disabled>已添加</button>'
      : `<button type="button" class="btn-primary" data-quick="${mode}" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">添加</button>`;
    return `<div class="row" style="justify-content: space-between; border-bottom: 1px solid var(--border-light); padding: 8px 0;">
      <div><strong>${esc(item.name)}</strong> <span style="font-size: 11px; background: rgba(142,142,147,0.14); padding: 1px 6px; border-radius: 6px; margin: 0 4px;">${esc(item.category || '通用')}</span> <span style="color: var(--text-secondary); margin-left: 2px;">${esc(item.description)}</span></div>
      ${button}
    </div>`;
  }).join("");
}

function softwareTable(mode) {
  const rows = state[mode === "portable" ? "portable" : "packages"] || [];
  const isPortable = mode === "portable";
  const title = isPortable ? "免安装软件管理" : "安装包软件管理";
  const hasAnyUpdate = rows.some(r => r.update);

  const rowsHtml = rows.map(row => {
    const isSel = selected[mode] === row.id ? "selected" : "";
    const updateHtml = row.update ? `<span class="red-dot"></span><span style="color: var(--danger); font-weight: 600;">${esc(row.latest)}</span>` : esc(row.latest || "尚未检查");
    const nameHtml = row.update ? `<span class="red-dot"></span><strong>${esc(row.name)}</strong>` : `<strong>${esc(row.name)}</strong>`;
    const actionBtn = isPortable
      ? `<button type="button" class="${row.update ? 'btn-primary' : ''}" data-row-install="${mode}" data-id="${esc(row.id)}">${row.update ? '升级' : (row.local ? '重装' : '安装')}</button>`
      : `<button type="button" class="${row.update ? 'btn-primary' : ''}" data-row-install="${mode}" data-id="${esc(row.id)}">下载打开</button>`;
    const runBtn = (isPortable && row.local) ? `<button type="button" data-row-launch="${esc(row.id)}">运行</button>` : '';

    return `<tr data-id="${esc(row.id)}" data-mode="${mode}" class="${isSel}">
      <td>${nameHtml}</td>
      <td><a href="${esc(row.repository)}" target="_blank">${esc(row.repository)}</a></td>
      <td>${esc(row.local || "-")}</td>
      <td>${updateHtml}</td>
      ${isPortable ? `<td><input type="text" value="${esc(row.directory)}" data-edit-dir="${esc(row.id)}" style="width: 100%;"></td>` : ""}
      <td>
        <div class="actions" style="margin: 0;">
          ${runBtn}
          <button type="button" data-row-check="${mode}" data-id="${esc(row.id)}">检查</button>
          ${actionBtn}
        </div>
      </td>
    </tr>`;
  }).join("");

  return `
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
      <h3 style="margin: 0; font-size: 17px;">${title}</h3>
      <div class="actions" style="margin: 0;">
        <button type="button" data-check-all="${mode}">全部检查</button>
        ${hasAnyUpdate ? `<button type="button" class="btn-primary" data-upgrade-all="${mode}">一键升级所有</button>` : ''}
        ${isPortable ? `<button type="button" data-launch-selected="${mode}">运行软件</button>` : ''}
        <button type="button" data-open-selected="${mode}">${isPortable ? '打开软件目录' : '打开安装包'}</button>
        ${isPortable ? `<button type="button" data-link-cli="${mode}" title="软链接至 ~/.local/bin">终端命令软链</button>` : `<button type="button" class="btn-primary" data-install-dmg="${mode}" title="挂载 DMG 自动提取至 /Applications">安装至 Applications</button>`}
        <button type="button" data-open-terminal="${mode}">终端打开</button>
        <button type="button" data-reveal-finder="${mode}">访达定位</button>
        ${isPortable ? `<button type="button" data-clear-backups="${mode}">清空备份</button>` : `<button type="button" data-delete-file="${mode}">删除下载包</button>`}
        <button type="button" data-remove="${mode}">仅移出列表</button>
        ${isPortable ? `<button type="button" class="btn-danger" data-uninstall="${mode}">卸载并删除文件</button>` : ''}
      </div>
    </div>
    <div class="table-box">
      <table>
        <thead>
          <tr>
            <th style="width: 140px;">软件名称</th>
            <th>GitHub 仓库</th>
            <th style="width: 110px;">本地版本</th>
            <th style="width: 130px;">最新版本</th>
            ${isPortable ? '<th>解压目录</th>' : ''}
            <th style="width: 170px;">操作</th>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml || `<tr><td colspan="${isPortable ? 6 : 5}" style="text-align: center; color: var(--text-secondary); padding: 24px;">暂无软件记录，请在下方添加或从软件库选择。</td></tr>`}
        </tbody>
      </table>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-top: 16px;">
      <div style="background: rgba(142, 142, 147, 0.08); border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
          <h4 style="margin: 0; font-size: 14px;">添加新软件</h4>
          <button type="button" data-open-search="${mode}" style="font-size: 12px; padding: 3px 8px;">搜索 GitHub</button>
        </div>
        <div class="row"><span>名称：</span><input id="add-name-${mode}" type="text" placeholder="例如 ripgrep" style="flex: 1;"></div>
        <div class="row"><span>仓库：</span><input id="add-repo-${mode}" type="text" placeholder="https://github.com/作者/仓库" style="flex: 1;"></div>
        ${isPortable ? `
          <div class="row"><span>解压目录：</span><input id="add-dir-${mode}" type="text" placeholder="例如 ~/Applications/ripgrep" style="flex: 1;"></div>
          <div class="row"><span>保留路径：</span><input id="add-keep-${mode}" type="text" placeholder="保留配置文件（选填）" style="flex: 1;"></div>
        ` : ''}
        <div class="actions" style="margin-top: 12px;"><button type="button" class="btn-primary" data-add="${mode}">添加到列表</button></div>
      </div>

      <div style="background: rgba(142, 142, 147, 0.08); border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <h4 style="margin: 0; font-size: 14px;">内置软件库</h4>
          <input type="text" id="lib-query-${mode}" data-lib-filter="${mode}" placeholder="搜索推荐软件…" style="width: 140px;">
        </div>
        ${categoryPills(mode)}
        <div id="lib-list-${mode}" style="max-height: 180px; overflow-y: auto; padding-right: 4px;">
          ${libraryList(mode, "")}
        </div>
      </div>
    </div>`;
}

function render() {
  if (!state) return;
  document.getElementById("proxy-badge").textContent = state.proxy_label || (state.proxy_enabled ? "代理：已启用" : "代理：直连");
  document.getElementById("proxy-enabled").checked = Boolean(state.proxy_enabled);
  document.getElementById("proxy-url").value = state.proxy || "";
  document.getElementById("download-workers").value = state.download_workers || 4;
  document.getElementById("prerelease-enabled").checked = Boolean(state.prerelease);
  document.getElementById("download-dir").value = state.download_directory || "";

  document.getElementById("panel-cli").innerHTML = projectCard("cli");
  document.getElementById("panel-plus").innerHTML = projectCard("plus");
  document.getElementById("panel-portable").innerHTML = softwareTable("portable");
  document.getElementById("panel-package").innerHTML = softwareTable("package");

  const job = state.job;
  const statusEl = document.getElementById("status");
  const cancelBtn = document.getElementById("cancel");
  const progressEl = document.getElementById("progress");
  const logEl = document.getElementById("log");

  if (job && !job.done) {
    statusEl.textContent = "正在处理任务…";
    cancelBtn.classList.remove("hidden");
    progressEl.style.width = (job.progress || 0) + "%";
  } else {
    statusEl.textContent = job && job.error ? "任务失败：" + job.error : "就绪";
    cancelBtn.classList.add("hidden");
    progressEl.style.width = (job && !job.error) ? "100%" : "0%";
  }
  if (job && job.lines && job.lines.length) {
    logEl.textContent = job.lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }
}

async function refresh() {
  try {
    if (isTauri) {
      state = await window.__TAURI__.core.invoke("get_state");
    } else {
      const res = await fetch("/api/state");
      state = await res.json();
    }
    render();
  } catch (e) {
    document.getElementById("status").textContent = "无法连接管理器：" + e.message;
  }
}

async function poll(jobId) {
  while (true) {
    try {
      let job = null;
      if (isTauri) {
        job = await window.__TAURI__.core.invoke("get_job", {jobId: String(jobId)});
      } else {
        const res = await fetch("/api/job/" + jobId);
        job = await res.json();
      }
      if (state) state.job = job;
      render();
      if (job && job.done) {
        await refresh();
        break;
      }
    } catch (_) {}
    await new Promise(r => setTimeout(r, 450));
  }
}

// 选项卡切换
document.querySelectorAll("nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    tabs.forEach(tab => {
      document.getElementById("panel-" + tab).classList.toggle("hidden", tab !== btn.dataset.tab);
    });
  });
});

// 设置展开切换
document.getElementById("settings-toggle").addEventListener("click", () => {
  const panel = document.getElementById("settings");
  panel.classList.toggle("hidden");
});

// 保存设置
document.getElementById("save-settings").addEventListener("click", async () => {
  try {
    state = await api("/api/settings", {
      proxy_enabled: document.getElementById("proxy-enabled").checked,
      proxy: document.getElementById("proxy-url").value,
      download_workers: parseInt(document.getElementById("download-workers").value, 10),
      prerelease: document.getElementById("prerelease-enabled").checked,
      download_directory: document.getElementById("download-dir").value
    });
    render();
    alert("设置已保存");
  } catch (error) { alert(error.message); }
});

// 检查管理器更新
document.getElementById("check-manager-update").addEventListener("click", async () => {
  const statusEl = document.getElementById("manager-update-status");
  statusEl.textContent = "正在检查更新…";
  try {
    const res = await api("/api/manager/check-update");
    if (res.has_update) {
      statusEl.innerHTML = `<span style="color: var(--danger); font-weight: 600;">发现新版本 v${esc(res.latest)}</span> <a href="${esc(res.url)}" target="_blank">前往下载</a>`;
    } else if (res.latest) {
      statusEl.textContent = `当前已是最新版 (v${esc(res.current)})`;
    } else if (res.error) {
      statusEl.textContent = `检查失败：${res.error}`;
    } else {
      statusEl.textContent = `当前版本：v${esc(res.current)}`;
    }
  } catch (err) {
    statusEl.textContent = "检查出错：" + err.message;
  }
});

// 导出配置
document.getElementById("export-settings-btn").addEventListener("click", async () => {
  try {
    const res = await api("/api/settings/export");
    const blob = new Blob([res.config], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "cpa-mac-settings.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) { alert("导出失败：" + err.message); }
});

// 导入配置
document.getElementById("import-settings-btn").addEventListener("click", async () => {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".json";
  input.onchange = async () => {
    if (!input.files || !input.files[0]) return;
    const text = await input.files[0].text();
    try {
      state = await api("/api/settings/import", {config: text});
      render();
      alert("配置导入成功！");
    } catch (err) { alert("导入失败：" + err.message); }
  };
  input.click();
});

// 取消正在进行的任务
document.getElementById("cancel").addEventListener("click", async () => {
  try { await api("/api/cancel"); } catch (error) { alert(error.message); }
});

// GitHub 搜索弹窗逻辑
function openSearchModal(mode) {
  searchModalMode = mode;
  document.getElementById("search-modal").classList.remove("hidden");
  document.getElementById("modal-search-input").focus();
}

function closeSearchModal() {
  document.getElementById("search-modal").classList.add("hidden");
  searchModalMode = null;
}

document.getElementById("modal-close").addEventListener("click", closeSearchModal);

async function doSearch() {
  const q = document.getElementById("modal-search-input").value.trim();
  if (!q) return;
  const container = document.getElementById("modal-search-results");
  container.innerHTML = "<p style='color: var(--text-secondary); text-align: center; margin: 20px 0;'>正在搜索 GitHub…</p>";
  try {
    const res = await api("/api/search", {query: q});
    const results = res.results || [];
    if (!results.length) {
      container.innerHTML = "<p style='color: var(--text-secondary); text-align: center; margin: 20px 0;'>未找到匹配的仓库。</p>";
      return;
    }
    container.innerHTML = results.map(item => `
      <div style="border-bottom: 1px solid var(--border-light); padding: 10px 0; display: flex; justify-content: space-between; align-items: center;">
        <div style="flex: 1; padding-right: 12px;">
          <div style="font-weight: 600;">${esc(item.name)} <span style="font-size: 12px; color: var(--text-secondary); font-weight: normal;">★ ${item.stars || 0}</span></div>
          <div style="font-size: 12.5px; color: var(--text-secondary); margin-top: 2px;">${esc(item.description || '暂无描述')}</div>
          <div style="font-size: 12px; color: var(--primary); margin-top: 2px;">${esc(item.repository)}</div>
        </div>
        <button type="button" class="btn-primary select-search-result" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">选用</button>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p style="color: var(--danger); text-align: center; margin: 20px 0;">搜索出错：${esc(err.message)}</p>`;
  }
}

document.getElementById("modal-search-btn").addEventListener("click", doSearch);
document.getElementById("modal-search-input").addEventListener("keydown", e => {
  if (e.key === "Enter") doSearch();
});

// 全局委托事件监听
document.body.addEventListener("click", async event => {
  // 1. GitHub 搜索结果选用
  if (event.target.classList.contains("select-search-result")) {
    const name = event.target.dataset.name;
    const repo = event.target.dataset.repo;
    if (searchModalMode) {
      const nameInput = document.getElementById("add-name-" + searchModalMode);
      const repoInput = document.getElementById("add-repo-" + searchModalMode);
      if (nameInput) nameInput.value = name;
      if (repoInput) repoInput.value = repo;
      if (searchModalMode === "portable") {
        const dirInput = document.getElementById("add-dir-portable");
        if (dirInput && !dirInput.value) dirInput.value = "~/Applications/" + name;
      }
    }
    closeSearchModal();
    return;
  }

  // 2. 打开 GitHub 搜索弹窗
  if (event.target.dataset.openSearch) {
    openSearchModal(event.target.dataset.openSearch);
    return;
  }

  // 3. 分类标签切换
  if (event.target.dataset.catSelect) {
    const cat = event.target.dataset.catSelect;
    const mode = event.target.dataset.catMode;
    currentCategory[mode] = cat;
    const list = document.getElementById("lib-list-" + mode);
    const qInput = document.getElementById("lib-query-" + mode);
    if (list) list.innerHTML = libraryList(mode, qInput ? qInput.value : "");
    const container = event.target.closest(".category-pills");
    if (container) {
      container.querySelectorAll(".pill-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.catSelect === cat);
      });
    }
    return;
  }

  // 4. 管理员 Key 复制与录入
  if (event.target.dataset.copyKey !== undefined) {
    const keyVal = event.target.dataset.copyKey;
    if (!keyVal) return alert("尚未捕获到 Key，请先启动服务生成。");
    try {
      await navigator.clipboard.writeText(keyVal);
      alert("管理员登录 Key 已复制到剪贴板！");
    } catch (_) {
      const input = document.getElementById("plus-admin-key");
      if (input) { input.select(); document.execCommand("copy"); alert("登录 Key 已复制！"); }
    }
    return;
  }
  if (event.target.dataset.enterKey) {
    const key = event.target.dataset.enterKey;
    const val = prompt("请输入已有管理员登录 Key：");
    if (val && val.trim()) {
      try {
        await api("/api/project/" + key + "/save-key", {key: val.trim()});
        await refresh();
        alert("Key 保存成功！");
      } catch (err) { alert(err.message); }
    }
    return;
  }

  // 5. 目录选择与打开
  if (event.target.dataset.browse) {
    const key = event.target.dataset.browse;
    const res = await api("/api/choose-directory");
    if (res.path) {
      document.getElementById("dir-" + key).value = res.path;
      state = await api("/api/project/" + key + "/directory", {directory: res.path});
      render();
    }
    return;
  }
  if (event.target.id === "browse-download") {
    const res = await api("/api/choose-directory");
    if (res.path) {
      document.getElementById("download-dir").value = res.path;
    }
    return;
  }
  if (event.target.dataset.open) {
    await api("/api/open", {path: event.target.dataset.open});
    return;
  }

  // 6. 项目控制 (start, stop, restart, check, install, open_config, open_log, clear_backups)
  if (event.target.dataset.project) {
    const key = event.target.dataset.project;
    const action = event.target.dataset.action;
    if (action === "clear_backups") {
      if (!confirm("确定清空当前服务的历史安装备份？清空后无法回滚旧版本。")) return;
      try {
        const res = await api("/api/project/" + key + "/clear-backups");
        await refresh();
        alert(`已清理 ${res.deleted || 0} 个历史安装备份。`);
      } catch (err) { alert(err.message); }
      return;
    }
    if (action === "open_config") {
      try { await api("/api/project/" + key + "/open-config"); } catch (err) { alert(err.message); }
      return;
    }
    if (action === "open_log") {
      try { await api("/api/project/" + key + "/open-log"); } catch (err) { alert(err.message); }
      return;
    }
    if (action === "enable_launchd") {
      try {
        await api("/api/project/" + key + "/enable-launchd");
        await refresh();
        alert("已成功配置为 macOS 开机自启常驻守护进程！");
      } catch (err) { alert("配置守护失败：" + err.message); }
      return;
    }
    if (action === "disable_launchd") {
      try {
        await api("/api/project/" + key + "/disable-launchd");
        await refresh();
        alert("已取消开机常驻守护进程。");
      } catch (err) { alert("停用守护失败：" + err.message); }
      return;
    }
    if (action === "release_port") {
      try {
        const res = await api("/api/project/" + key + "/release-port");
        await refresh();
        alert(res.result && res.result.released ? `端口 ${res.result.port} 已成功释放！` : "释放端口失败。");
      } catch (err) { alert("释放端口出错：" + err.message); }
      return;
    }
    if (action === "tail_log") {
      try { await api("/api/project/" + key + "/tail-log"); } catch (err) { alert(err.message); }
      return;
    }
    if (action === "terminal") {
      try { await api("/api/project/" + key + "/terminal"); } catch (err) { alert(err.message); }
      return;
    }
    if (action === "reveal") {
      try { await api("/api/project/" + key + "/reveal"); } catch (err) { alert(err.message); }
      return;
    }
    try {
      const data = await api("/api/project/" + key + "/" + action, {force: action === "check"});
      if (data && data.job_id) poll(data.job_id);
      else await refresh();
    } catch (error) { alert(error.message); }
    return;
  }

  // 7. 打开管理后台
  if (event.target.dataset.openPage) {
    const endpoint = (state.projects[event.target.dataset.openPage] || {}).endpoint;
    if (!endpoint) return alert("服务未启动或未监听端口，请先启动服务。");
    window.open(endpoint, "_blank");
    return;
  }

  // 8. 软件库快速添加
  if (event.target.dataset.quick) {
    const quickMode = event.target.dataset.quick;
    const directory = quickMode === "portable" ? "~/Applications/" + event.target.dataset.name : "";
    try {
      state = await api("/api/software/" + quickMode + "/add", {
        name: event.target.dataset.name,
        repository: event.target.dataset.repo,
        directory,
        preserve: ""
      });
      render();
    } catch (error) { alert(error.message); }
    return;
  }

  // 9. 单行检查与安装
  if (event.target.dataset.rowCheck || event.target.dataset.rowInstall) {
    const rowMode = event.target.dataset.rowCheck || event.target.dataset.rowInstall;
    const rowAction = event.target.dataset.rowCheck ? "check" : "install";
    selected[rowMode] = event.target.dataset.id;
    try {
      const data = await api("/api/software/" + rowMode + "/" + rowAction, {id: event.target.dataset.id, force: rowAction === "check"});
      poll(data.job_id);
    } catch (error) { alert(error.message); }
    return;
  }

  // 10. 行内直接运行
  if (event.target.dataset.rowLaunch) {
    try {
      const res = await api("/api/software/portable/launch", {id: event.target.dataset.rowLaunch});
      alert(res.message || "已启动软件");
    } catch (err) { alert(err.message); }
    return;
  }

  // 11. 自定义添加软件表单提交
  if (event.target.dataset.add) {
    const addMode = event.target.dataset.add;
    try {
      state = await api("/api/software/" + addMode + "/add", {
        name: document.getElementById("add-name-" + addMode).value,
        repository: document.getElementById("add-repo-" + addMode).value,
        directory: addMode === "portable" ? document.getElementById("add-dir-portable").value : "",
        preserve: addMode === "portable" ? document.getElementById("add-keep-portable").value : ""
      });
      render();
    } catch (error) { alert(error.message); }
    return;
  }

  // 12. 全部检查
  if (event.target.dataset.checkAll) {
    try {
      const data = await api("/api/software/" + event.target.dataset.checkAll + "/check", {all: true, force: true});
      poll(data.job_id);
    } catch (error) { alert(error.message); }
    return;
  }

  // 13. 一键全部升级
  if (event.target.dataset.upgradeAll) {
    const upMode = event.target.dataset.upgradeAll;
    try {
      const data = await api("/api/software/" + upMode + "/upgrade-all", {});
      poll(data.job_id);
    } catch (error) { alert(error.message); }
    return;
  }

  // 14. 移除记录
  if (event.target.dataset.remove) {
    const removeMode = event.target.dataset.remove;
    const id = selected[removeMode];
    if (!id) return alert("请先在列表中选中一行。");
    if (!confirm("仅从列表移出配置记录，保留已安装的文件。")) return;
    state = await api("/api/software/" + removeMode + "/remove", {id});
    selected[removeMode] = "";
    render();
    return;
  }

  // 15. 卸载并删除文件
  if (event.target.dataset.uninstall) {
    const unMode = event.target.dataset.uninstall;
    const id = selected[unMode];
    if (!id) return alert("请先在列表中选中一行。");
    if (!confirm("警告：将彻底删除软件解压目录中的所有文件并移除记录，此操作不可恢复！确定卸载？")) return;
    try {
      state = await api("/api/software/" + unMode + "/uninstall", {id});
      selected[unMode] = "";
      render();
      alert("软件已彻底卸载并清理文件。");
    } catch (err) { alert(err.message); }
    return;
  }

  // 16. 删除安装包文件
  if (event.target.dataset.deleteFile) {
    const delMode = event.target.dataset.deleteFile;
    const id = selected[delMode];
    if (!id) return alert("请先在列表中选中一行。");
    if (!confirm("确定从下载目录中删除已下载的安装包文件？")) return;
    try {
      await api("/api/software/" + delMode + "/delete-file", {id});
      alert("安装包文件已删除。");
    } catch (err) { alert(err.message); }
    return;
  }

  // 17. 启动选中软件
  if (event.target.dataset.launchSelected) {
    const id = selected.portable;
    if (!id) return alert("请先在列表中选中一行。");
    try {
      const res = await api("/api/software/portable/launch", {id});
      alert(res.message || "已启动软件");
    } catch (err) { alert(err.message); }
    return;
  }

  // 18. 清空免安装备份
  if (event.target.dataset.clearBackups) {
    const id = selected.portable;
    if (!id) return alert("请先在列表中选中一行。");
    try {
      const res = await api("/api/software/portable/clear-backups", {id});
      alert(`已清理 ${res.deleted || 0} 个历史安装备份。`);
    } catch (err) { alert(err.message); }
    return;
  }

  // 19. 打开选中目录 / 安装包
  if (event.target.dataset.openSelected) {
    const openMode = event.target.dataset.openSelected;
    const row = (state[openMode === "portable" ? "portable" : "packages"] || []).find(item => item.id === selected[openMode]);
    if (!row) return alert("请先在列表中选中一行。");
    if (openMode === "portable") {
      await api("/api/open", {path: row.directory});
    } else {
      if (row.asset) {
        const full = (state.download_directory || "").replace(/\/$/, "") + "/" + row.asset;
        await api("/api/open", {path: full});
      } else {
        await api("/api/open", {path: state.download_directory});
      }
    }
    return;
  }

  // 19.1 免安装软件链接至 ~/.local/bin
  if (event.target.dataset.linkCli) {
    const mode = event.target.dataset.linkCli;
    const row = (state.portable || []).find(item => item.id === selected[mode]);
    if (!row) return alert("请先在列表中选中一行免安装软件。");
    try {
      const res = await api("/api/software/portable/link-cli", {id: row.id});
      alert(`已成功创建软链接: ${res.link}
您现在可以在 Mac 终端中直接运行该命令！`);
    } catch (err) { alert("创建软链接失败：" + err.message); }
    return;
  }

  // 19.2 DMG 一键自动安装至 /Applications
  if (event.target.dataset.installDmg) {
    const mode = event.target.dataset.installDmg;
    const row = (state.packages || []).find(item => item.id === selected[mode]);
    if (!row) return alert("请先在列表中选中一行安装包记录。");
    if (!row.asset || !row.asset.toLowerCase().endsWith(".dmg")) {
      return alert("选中的记录不是 .dmg 镜像包，请先检查并下载对应的 DMG 文件。");
    }
    try {
      const res = await api("/api/software/package/install-dmg", {id: row.id});
      alert(`🎉 应用程序已成功挂载并提取安装至：
${res.app}
已自动解除 Gatekeeper 隔离限制，可在“启动台”或 /Applications 中直接启动！`);
    } catch (err) { alert("自动安装 DMG 失败：" + err.message); }
    return;
  }

  // 19.3 终端中打开
  if (event.target.dataset.openTerminal) {
    const mode = event.target.dataset.openTerminal;
    const row = (state[mode === "portable" ? "portable" : "packages"] || []).find(item => item.id === selected[mode]);
    try {
      await api("/api/software/" + (mode === "portable" ? "portable" : "package") + "/terminal", {id: row ? row.id : ""});
    } catch (err) { alert(err.message); }
    return;
  }

  // 19.4 访达中高亮定位
  if (event.target.dataset.revealFinder) {
    const mode = event.target.dataset.revealFinder;
    const row = (state[mode === "portable" ? "portable" : "packages"] || []).find(item => item.id === selected[mode]);
    if (!row) return alert("请先在列表中选中一行。");
    try {
      await api("/api/software/" + (mode === "portable" ? "portable" : "package") + "/reveal", {id: row.id});
    } catch (err) { alert(err.message); }
    return;
  }
});

// 筛选输入监听
document.body.addEventListener("input", event => {
  const filterMode = event.target.dataset.libFilter;
  if (!filterMode) return;
  const list = document.getElementById("lib-list-" + filterMode);
  if (list) list.innerHTML = libraryList(filterMode, event.target.value);
});

// 表单修改即时保存监听
document.body.addEventListener("change", async event => {
  if (event.target.id === "download-workers") {
    let val = parseInt(event.target.value, 10);
    if (isNaN(val) || val < 1) val = 1;
    if (val > 16) val = 16;
    event.target.value = val;
    return;
  }
  if (event.target.id === "dir-cli" || event.target.id === "dir-plus") {
    const key = event.target.id.slice(4);
    try {
      state = await api("/api/project/" + key + "/directory", {directory: event.target.value});
      render();
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.editDir) {
    try {
      state = await api("/api/software/portable/update", {id: event.target.dataset.editDir, directory: event.target.value});
      render();
    } catch (error) { alert(error.message); }
  }
});

// 行选择
document.body.addEventListener("click", event => {
  if (event.target.closest("button, a, input")) return;
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  selected[row.dataset.mode] = row.dataset.id;
  render();
});

refresh();

// Mac Doctor 诊断弹窗
const doctorBtn = document.getElementById("mac-doctor-btn");
const doctorModal = document.getElementById("doctor-modal");
const doctorClose = document.getElementById("doctor-modal-close");

if (doctorClose) {
  doctorClose.addEventListener("click", () => {
    doctorModal.classList.add("hidden");
  });
}

if (doctorBtn) {
  doctorBtn.addEventListener("click", async () => {
    doctorModal.classList.remove("hidden");
    const container = document.getElementById("doctor-modal-content");
    container.innerHTML = "<p style='color: var(--text-secondary); text-align: center; margin: 20px 0;'>正在深度诊断 Mac 系统环境…</p>";
    try {
      const doc = await api("/api/macos/doctor");
      const archText = doc.is_apple_silicon ? "Apple Silicon (arm64)" : `Intel x86_64 (${doc.architecture})`;
      const p8317 = doc.ports["8317"];
      const p18317 = doc.ports["18317"];
      const p8317Str = p8317 && p8317.command ? `<span style="color: var(--danger);">被 ${esc(p8317.command)} (PID ${p8317.pid}) 占用</span>` : "<span style="color: var(--success);">空闲可用</span>";
      const p18317Str = p18317 && p18317.command ? `<span style="color: var(--danger);">被 ${esc(p18317.command)} (PID ${p18317.pid}) 占用</span>` : "<span style="color: var(--success);">空闲可用</span>";
      const pathStr = doc.has_local_bin_in_path ? "<span style="color: var(--success);">已包含 (~/.local/bin)</span>" : "<span style="color: var(--warning);">未包含 (建议在 ~/.zshrc 中添加 PATH)</span>";
      const cliLa = doc.launch_agents.cli.loaded ? "<span style="color: var(--success);">运行中</span>" : "未启用";
      const plusLa = doc.launch_agents.plus.loaded ? "<span style="color: var(--success);">运行中</span>" : "未启用";

      container.innerHTML = `
        <div style="display: grid; grid-template-columns: 140px 1fr; row-gap: 10px; font-size: 13.5px;">
          <strong style="color: var(--text-secondary);">操作系统：</strong> <span>macOS ${esc(doc.mac_version)} (内核 ${esc(doc.kernel)})</span>
          <strong style="color: var(--text-secondary);">芯片架构：</strong> <span><strong>${archText}</strong></span>
          <strong style="color: var(--text-secondary);">可用磁盘空间：</strong> <span>${doc.free_disk_gb} GB</span>
          <strong style="color: var(--text-secondary);">CLI 端口 (8317)：</strong> <span>${p8317Str}</span>
          <strong style="color: var(--text-secondary);">Plus 端口 (18317)：</strong> <span>${p18317Str}</span>
          <strong style="color: var(--text-secondary);">环境变量 PATH：</strong> <span>${pathStr}</span>
          <strong style="color: var(--text-secondary);">CLI 守护进程：</strong> <span>${cliLa}</span>
          <strong style="color: var(--text-secondary);">Plus 守护进程：</strong> <span>${plusLa}</span>
        </div>
        <div style="margin-top: 16px; padding: 10px 12px; background: rgba(0, 122, 255, 0.08); border-radius: 6px; font-size: 12.5px; color: var(--text-secondary);">
          💡 提示: 本管理器原生适配 macOS 架构，免安装软件自动清除 Gatekeeper 隔离属性 (quarantine)，支持一键自动安装 DMG 应用至 /Applications。
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<p style="color: var(--danger); text-align: center; margin: 20px 0;">诊断出错: ${esc(err.message)}</p>`;
    }
  });
}
