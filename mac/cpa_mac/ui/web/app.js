let state = null;
let selected = {portable: "", package: ""};
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
      "/api/open": "open_path"
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

  return `
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <h3 style="margin: 0; font-size: 17px;">${esc(item.title)}</h3>
      <span class="${statusClass}">${esc(item.service)}</span>
    </div>
    <p class="meta-text" style="margin-top: 6px;">项目主页：<a href="${esc(item.repo)}" target="_blank">${esc(item.repo)}</a></p>
    <div class="row" style="margin: 12px 0;">
      <span style="font-weight: 500;">安装目录：</span>
      <input id="dir-${key}" type="text" value="${esc(item.directory)}" style="flex: 1;">
      <button type="button" data-browse="${key}">选择</button>
      <button type="button" data-open="${esc(item.directory)}">打开目录</button>
    </div>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin: 12px 0; background: rgba(142, 142, 147, 0.08); padding: 12px 14px; border-radius: 8px;">
      <div><span style="color: var(--text-secondary);">本地版本：</span><strong>${esc(item.local || "未安装")}</strong></div>
      <div><span style="color: var(--text-secondary);">最新版本：</span>${latestTag}</div>
      <div><span style="color: var(--text-secondary);">附件文件：</span>${esc(item.asset || "尚未检查")}</div>
      <div><span style="color: var(--text-secondary);">管理后台：</span>${item.endpoint ? `<a href="${esc(item.endpoint)}" target="_blank">${esc(item.endpoint)}（打开页面）</a>` : "启动后可打开页面"}</div>
    </div>
    <p class="meta-text">首次安装会写入仅监听本机的默认配置，已有配置文件不会被覆盖。</p>
    <div class="actions" style="margin-top: 14px;">
      <button type="button" data-project="${key}" data-action="check">检查最新版</button>
      <button type="button" class="${installBtnClass}" data-project="${key}" data-action="install">${installBtnText}</button>
      ${mainServiceBtn}
    </div>`;
}

function listed(mode, repo) {
  const rows = state[mode === "portable" ? "portable" : "packages"] || [];
  return rows.some(row => String(row.repository).toLowerCase() === String(repo).toLowerCase());
}

function catalogItems(mode, query) {
  const want = mode === "portable" ? "免安装" : "安装包";
  const text = (query || "").toLowerCase();
  return (state.catalog || []).filter(item => item.mode === want && (!text || (item.name + " " + item.description + " " + item.repository).toLowerCase().includes(text)));
}

function libraryList(mode, query) {
  const items = catalogItems(mode, query);
  if (!items.length) return "<p style='color: var(--text-secondary);'>软件库没有匹配项。</p>";
  return items.map(item => {
    const button = listed(mode, item.repository)
      ? '<button type="button" disabled>已添加</button>'
      : `<button type="button" class="btn-primary" data-quick="${mode}" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">添加</button>`;
    return `<div class="row" style="justify-content: space-between; border-bottom: 1px solid var(--border-light); padding: 8px 0;">
      <div><strong>${esc(item.name)}</strong> <span style="color: var(--text-secondary); margin-left: 6px;">${esc(item.description)}</span></div>
      ${button}
    </div>`;
  }).join("");
}

function softwareTable(mode) {
  const rows = state[mode] || [];
  const isPortable = mode === "portable";
  const title = isPortable ? "免安装软件管理" : "安装包软件管理";
  const rowsHtml = rows.map(row => {
    const isSel = selected[mode] === row.id ? "selected" : "";
    const updateHtml = row.update ? `<span class="red-dot"></span><span style="color: var(--danger); font-weight: 600;">${esc(row.latest)}</span>` : esc(row.latest || "尚未检查");
    const nameHtml = row.update ? `<span class="red-dot"></span><strong>${esc(row.name)}</strong>` : `<strong>${esc(row.name)}</strong>`;
    const actionBtn = isPortable
      ? `<button type="button" class="${row.update ? 'btn-primary' : ''}" data-row-install="${mode}" data-id="${esc(row.id)}">${row.update ? '升级' : '安装'}</button>`
      : `<button type="button" class="${row.update ? 'btn-primary' : ''}" data-row-install="${mode}" data-id="${esc(row.id)}">下载安装</button>`;

    return `<tr data-id="${esc(row.id)}" data-mode="${mode}" class="${isSel}">
      <td>${nameHtml}</td>
      <td><a href="${esc(row.repository)}" target="_blank">${esc(row.repository)}</a></td>
      <td>${esc(row.local || "-")}</td>
      <td>${updateHtml}</td>
      ${isPortable ? `<td><input type="text" value="${esc(row.directory)}" data-edit-dir="${esc(row.id)}" style="width: 100%;"></td>` : ""}
      <td>
        <div class="actions" style="margin: 0;">
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
        <button type="button" data-open-selected="${mode}">打开目录</button>
        <button type="button" class="btn-danger" data-remove="${mode}">移除记录</button>
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
            <th style="width: 140px;">操作</th>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml || `<tr><td colspan="${isPortable ? 6 : 5}" style="text-align: center; color: var(--text-secondary); padding: 24px;">暂无软件记录，请在下方添加或从软件库选择。</td></tr>`}
        </tbody>
      </table>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-top: 16px;">
      <div style="background: rgba(142, 142, 147, 0.08); border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
        <h4 style="margin: 0 0 10px; font-size: 14px;">添加新软件</h4>
        <div class="row"><span>名称：</span><input id="add-name-${mode}" type="text" placeholder="例如 ripgrep"></div>
        <div class="row"><span>仓库：</span><input id="add-repo-${mode}" type="text" placeholder="https://github.com/作者/仓库"></div>
        ${isPortable ? `
          <div class="row"><span>解压目录：</span><input id="add-dir-${mode}" type="text" placeholder="例如 ~/Applications/ripgrep"></div>
          <div class="row"><span>保留路径：</span><input id="add-keep-${mode}" type="text" placeholder="保留配置文件（选填）"></div>
        ` : ''}
        <div class="actions"><button type="button" class="btn-primary" data-add="${mode}">添加到列表</button></div>
      </div>

      <div style="background: rgba(142, 142, 147, 0.08); border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <h4 style="margin: 0; font-size: 14px;">内置软件库</h4>
          <div style="display: flex; gap: 6px;">
            <input type="text" id="lib-query-${mode}" data-lib-filter="${mode}" placeholder="筛选软件库…" style="width: 140px;">
            <button type="button" data-github-search="${mode}">搜索 GitHub</button>
          </div>
        </div>
        <div id="lib-list-${mode}" style="max-height: 170px; overflow-y: auto; padding-right: 4px;">
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
        job = await window.__TAURI__.core.invoke("get_job", {jobId});
      } else {
        const res = await fetch("/api/job/" + jobId);
        job = await res.json();
      }
      if (state) state.job = job;
      render();
      if (job.done) {
        await refresh();
        break;
      }
    } catch (e) { break; }
    await new Promise(r => setTimeout(r, 600));
  }
}

document.body.addEventListener("click", async event => {
  const tabBtn = event.target.closest("nav button");
  if (tabBtn) {
    document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
    tabBtn.classList.add("active");
    const target = tabBtn.dataset.tab;
    tabs.forEach(t => {
      const panel = document.getElementById("panel-" + t);
      if (panel) panel.classList.toggle("hidden", t !== target);
    });
    return;
  }
  if (event.target.id === "settings-toggle") {
    document.getElementById("settings").classList.toggle("hidden");
    return;
  }
  if (event.target.id === "save-settings") {
    let workers = parseInt(document.getElementById("download-workers").value, 10);
    if (isNaN(workers) || workers < 1) workers = 1;
    if (workers > 16) workers = 16;
    document.getElementById("download-workers").value = workers;

    try {
      state = await api("/api/settings", {
        proxy_enabled: document.getElementById("proxy-enabled").checked,
        proxy: document.getElementById("proxy-url").value,
        download_workers: workers,
        prerelease: document.getElementById("prerelease-enabled").checked,
        download_directory: document.getElementById("download-dir").value
      });
      document.getElementById("settings").classList.add("hidden");
      render();
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.id === "cancel") {
    await api("/api/cancel");
    return;
  }
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
  if (event.target.dataset.project) {
    const key = event.target.dataset.project;
    const action = event.target.dataset.action;
    try {
      const data = await api("/api/project/" + key + "/" + action, {force: action === "check"});
      poll(data.job_id);
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.openPage) {
    const endpoint = (state.projects[event.target.dataset.openPage] || {}).endpoint;
    if (!endpoint) return alert("还没有可打开的地址，请先安装。");
    window.open(endpoint, "_blank");
    return;
  }
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
  if (event.target.dataset.checkAll) {
    try {
      const data = await api("/api/software/" + event.target.dataset.checkAll + "/check", {all: true, force: true});
      poll(data.job_id);
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.remove) {
    const removeMode = event.target.dataset.remove;
    const id = selected[removeMode];
    if (!id) return alert("请先选择一行。");
    if (!confirm("只从列表移除，不删除已安装文件。")) return;
    state = await api("/api/software/" + removeMode + "/remove", {id});
    selected[removeMode] = "";
    render();
    return;
  }
  if (event.target.dataset.openSelected) {
    const openMode = event.target.dataset.openSelected;
    const row = (state[openMode === "portable" ? "portable" : "packages"] || []).find(item => item.id === selected[openMode]);
    if (!row) return alert("请先选择一行。");
    await api("/api/open", {path: openMode === "portable" ? row.directory : state.download_directory});
  }
});

document.body.addEventListener("input", event => {
  const filterMode = event.target.dataset.libFilter;
  if (!filterMode) return;
  const list = document.getElementById("lib-list-" + filterMode);
  if (list) list.innerHTML = libraryList(filterMode, event.target.value);
});

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

document.body.addEventListener("click", event => {
  if (event.target.closest("button, a, input")) return;
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  selected[row.dataset.mode] = row.dataset.id;
  render();
});

refresh();