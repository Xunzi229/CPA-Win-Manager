"""Local browser UI for the Mac manager."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid

from cpa_mac.backends import github, service, software
from cpa_mac.config import CATALOG, MAC_ROOT, PROJECTS, SOURCE_VERSION, has_update, host_architecture
from cpa_mac.core.settings import support_dir
from cpa_mac.state import load_data, save_data

try:
    import fcntl
except ImportError:
    fcntl = None


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.done = False
        self.error = ""
        self.progress = 0
        self.lines = []
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def report(self, percent, message):
        with self.lock:
            if isinstance(percent, (int, float)):
                self.progress = max(0, min(100, int(percent)))
            if message:
                self.lines.append(time.strftime("%H:%M:%S ") + message)
                del self.lines[:-200]

    def finish(self, error=""):
        with self.lock:
            self.done = True
            self.error = error

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "done": self.done, "error": self.error,
                    "progress": self.progress, "lines": list(self.lines)}


class Store:
    def __init__(self):
        self.data = load_data()
        self.local = {}
        self.job = None
        self.lock = threading.Lock()

    def save(self):
        save_data(self.data)

    def proxy(self):
        return self.data["proxy"].strip() if self.data["proxy_enabled"] else ""

    def busy_job(self):
        job = self.job
        if job is None:
            return None
        with job.lock:
            return None if job.done else job

    def start(self, work):
        with self.lock:
            if self.busy_job() is not None:
                raise RuntimeError("已有任务在运行，请等它结束。")
            job = Job()
            self.job = job

        def runner():
            try:
                work(job.report)
                job.finish()
            except Exception as error:
                job.report(None, str(error))
                job.finish(str(error))

        threading.Thread(target=runner, daemon=True).start()
        return job.id


STORE = None


def get_store():
    global STORE
    if STORE is None:
        STORE = Store()
    return STORE


def project_state(store, key):
    spec = PROJECTS[key]
    profile = store.data[key]
    root = Path(profile["directory"]).expanduser()
    binary = root / spec["binary"]
    try:
        running = service.service_status(root, spec)
    except (OSError, RuntimeError) as error:
        running = str(error)
    endpoint = ""
    if binary.is_file() and (root / spec["config"]).is_file():
        try:
            host, port = service.read_endpoint(root, spec)
            endpoint = f"http://{host}:{port}/"
        except (RuntimeError, OSError):
            endpoint = ""
    local = store.local.get(key) or ""
    latest = profile.get("latest") or ""
    return {
        "key": key,
        "title": spec["title"],
        "repo": spec["repo"],
        "directory": profile["directory"],
        "latest": latest,
        "asset": profile.get("asset") or "",
        "local": local,
        "update": has_update(local, latest),
        "installed": binary.is_file(),
        "service": running,
        "endpoint": endpoint,
    }


def software_state(record, mode):
    local = ""
    if mode == "portable" and record.get("directory"):
        local = software.installed_tag(record["directory"])
    latest = record.get("latest") or ""
    return {**record, "local": local, "update": has_update(local, latest)}


def cancelled(store):
    job = store.job
    if job is not None and job.cancel.is_set():
        raise RuntimeError("任务已取消。")


def snapshot(store):
    job = store.job.snapshot() if store.job else None
    return {
        "version": SOURCE_VERSION,
        "proxy_enabled": store.data["proxy_enabled"],
        "proxy": store.data["proxy"],
        "proxy_label": "代理：已启用" if store.data["proxy_enabled"] else "代理：直连",
        "download_directory": store.data["download_directory"],
        "projects": {key: project_state(store, key) for key in PROJECTS},
        "portable": [software_state(item, "portable") for item in store.data["portable"]],
        "packages": [software_state(item, "package") for item in store.data["packages"]],
        "apps_directory": str(support_dir() / "apps"),
        "catalog": [{"mode": mode, "name": name, "description": desc, "repository": repo}
                    for mode, name, desc, repo in CATALOG],
        "job": job,
    }


def refresh_local(store, key):
    spec = PROJECTS[key]
    root = Path(store.data[key]["directory"]).expanduser()
    binary = root / spec["binary"]
    store.local[key] = service.local_version(root, spec) if binary.is_file() else ""


def check_project(store, key, report):
    spec = PROJECTS[key]
    root = store.data[key]["directory"]
    local, release, asset = service.check(spec, root, store.proxy(), host_architecture(), report)
    store.data[key]["latest"] = release["tag"]
    store.data[key]["asset"] = asset["name"]
    store.local[key] = local or ""
    store.save()
    if has_update(local, release["tag"]):
        report(100, f"可更新：{local} → {release['tag']}")
    elif local:
        report(100, f"本地 {local}，最新版 {release['tag']}。")
    else:
        report(100, f"尚未安装，最新版 {release['tag']}。")


def install_project(store, key, report):
    spec = PROJECTS[key]
    cancel = store.job.cancel if store.job else None
    tag, asset_name = service.install(spec, store.data[key]["directory"], store.proxy(), host_architecture(),
                                      report, MAC_ROOT, cancel)
    store.data[key]["latest"] = tag
    store.data[key]["asset"] = asset_name
    store.save()
    refresh_local(store, key)


def find_record(store, mode, identity):
    records = store.data["portable" if mode == "portable" else "packages"]
    return next((item for item in records if item["id"] == identity), None)


def check_record(store, mode, record, report):
    cancelled(store)
    report(None, f"正在检查 {record['name']}…")
    release = github.fetch_latest(record["repository"], store.proxy())
    asset = github.recommend(release["assets"], mode, host_architecture())
    if asset is None:
        raise RuntimeError(f"{record['name']} 的最新 Release 没有适合当前 Mac 的附件。")
    record["latest"] = release["tag"]
    record["asset"] = asset["name"]
    store.save()
    local = software.installed_tag(record["directory"]) if mode == "portable" else ""
    if has_update(local, release["tag"]):
        report(100, f"{record['name']} 可更新到 {release['tag']}，附件 {asset['name']}。")
    else:
        report(100, f"{record['name']} 最新版 {release['tag']}，附件 {asset['name']}。")
    return release, asset


def choose_directory():
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择目录")'],
        capture_output=True, text=True)
    if result.returncode:
        return ""
    return result.stdout.strip().rstrip("/")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = PAGE.encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/state":
            self._json(snapshot(get_store()))
            return
        if path.startswith("/api/job/"):
            job_id = path.rsplit("/", 1)[-1]
            job = get_store().job
            if job is None or job.id != job_id:
                self._json({"error": "任务不存在。"}, 404)
                return
            self._json(job.snapshot())
            return
        self._json({"error": "没有这个页面。"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() or "{}")
        except (json.JSONDecodeError, UnicodeError):
            self._json({"error": "请求格式不正确。"}, 400)
            return
        if not isinstance(body, dict):
            body = {}
        store = get_store()
        try:
            result = self._post(path, body, store)
        except (RuntimeError, ValueError, OSError) as error:
            self._json({"error": str(error)}, 400)
            return
        self._json(result or snapshot(store))

    def _post(self, path, body, store):
        if path == "/api/settings":
            enabled = body.get("proxy_enabled")
            if type(enabled) is not bool:
                raise ValueError("请选择是否启用代理。")
            proxy = body.get("proxy") if isinstance(body.get("proxy"), str) else ""
            folder = body.get("download_directory") if isinstance(body.get("download_directory"), str) else ""
            if enabled:
                from cpa_mac.core.network import network
                network(proxy)
            if not folder.strip():
                raise ValueError("请填写下载目录。")
            store.data["proxy_enabled"] = enabled
            store.data["proxy"] = proxy.strip() or "http://127.0.0.1:7890"
            store.data["download_directory"] = folder.strip()
            store.save()
            return None
        if path == "/api/cancel":
            job = store.job
            if job is None or job.done:
                raise RuntimeError("没有正在执行的任务。")
            job.cancel.set()
            job.report(None, "正在取消…")
            return {"ok": True}
        if path == "/api/search":
            query = body.get("query") if isinstance(body.get("query"), str) else ""
            entries, total = github.search_repositories(query, store.proxy())
            return {"results": entries, "total": total}
        if path == "/api/choose-directory":
            return {"path": choose_directory()}
        if path == "/api/open":
            target = body.get("path") if isinstance(body.get("path"), str) else ""
            if not target.strip():
                raise ValueError("没有可打开的目录。")
            folder = Path(target).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(folder)], check=False)
            return {"ok": True}
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "project"] and parts[2] in PROJECTS:
            return self._project(parts[2], parts[3], body, store)
        if len(parts) == 4 and parts[:2] == ["api", "software"] and parts[2] in ("portable", "package"):
            return self._software(parts[2], parts[3], body, store)
        raise ValueError("没有这个操作。")

    def _project(self, key, action, body, store):
        if action == "directory":
            directory = body.get("directory") if isinstance(body.get("directory"), str) else ""
            if not directory.strip():
                raise ValueError("请填写安装目录。")
            store.data[key]["directory"] = directory.strip()
            store.local.pop(key, None)
            store.save()
            refresh_local(store, key)
            return None
        if action == "check":
            return {"job_id": store.start(lambda report: check_project(store, key, report))}
        if action == "install":
            return {"job_id": store.start(lambda report: install_project(store, key, report))}
        if action in ("start", "stop", "restart"):
            spec = PROJECTS[key]
            root = store.data[key]["directory"]
            return {"job_id": store.start(lambda report: service.control(action, root, spec, report))}
        raise ValueError("没有这个操作。")

    def _software(self, mode, action, body, store):
        records = store.data["portable" if mode == "portable" else "packages"]
        if action == "add":
            name = body.get("name") if isinstance(body.get("name"), str) else ""
            repo = body.get("repository") if isinstance(body.get("repository"), str) else ""
            directory = body.get("directory") if isinstance(body.get("directory"), str) else ""
            preserve = body.get("preserve") if isinstance(body.get("preserve"), str) else ""
            name = name.strip()
            if not name:
                raise ValueError("请填写名称。")
            address = github.repository(repo)
            if any(item["repository"].casefold() == address.casefold() for item in records):
                raise ValueError("这个仓库已经在列表中。")
            if mode == "portable" and not directory.strip():
                raise ValueError("请填写安装目录。")
            software.parse_preserve(preserve)
            records.append({"id": uuid.uuid4().hex, "name": name, "repository": address,
                            "directory": directory.strip(), "preserve": preserve.strip(), "latest": "", "asset": ""})
            store.save()
            return None
        if action == "update":
            record = find_record(store, mode, body.get("id"))
            if record is None:
                raise ValueError("没有选中的软件。")
            if mode != "portable":
                raise ValueError("安装包使用设置里的下载目录。")
            directory = body.get("directory") if isinstance(body.get("directory"), str) else ""
            if not directory.strip():
                raise ValueError("请填写安装目录。")
            record["directory"] = str(service.assert_install_directory(directory, MAC_ROOT))
            store.save()
            return None
        if action == "remove":
            identity = body.get("id")
            if not any(item["id"] == identity for item in records):
                raise ValueError("没有选中的软件。")
            store.data["portable" if mode == "portable" else "packages"] = [
                item for item in records if item["id"] != identity]
            store.save()
            return None
        if action == "check":
            if body.get("all"):
                items = list(records)
                if not items:
                    raise ValueError("列表是空的。")

                def work(report):
                    for item in items:
                        cancelled(store)
                        check_record(store, mode, item, report)

                return {"job_id": store.start(work)}
            record = find_record(store, mode, body.get("id"))
            if record is None:
                raise ValueError("请先选择一行。")
            return {"job_id": store.start(lambda report: check_record(store, mode, record, report))}
        if action == "install":
            record = find_record(store, mode, body.get("id"))
            if record is None:
                raise ValueError("请先选择一行。")

            def work(report):
                release, asset = check_record(store, mode, record, report)
                cancel = store.job.cancel if store.job else None
                if mode == "portable":
                    software.install_portable(release, asset, record["directory"], store.proxy(), report,
                                              MAC_ROOT, record.get("preserve") or "", cancel)
                else:
                    software.install_package(release, asset, store.data["download_directory"], store.proxy(), report, cancel)

            return {"job_id": store.start(work)}
        raise ValueError("没有这个操作。")

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(payload, ensure_ascii=False).encode("utf-8"))


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>CPA Mac 管理器</title>
<style>
  body { margin: 0; font: 14px/1.5 "PingFang SC", sans-serif; background: #f5f5f7; color: #1d1d1f; }
  header, main { padding: 16px 20px; }
  header { display: flex; justify-content: space-between; align-items: center; }
  h1 { margin: 0; font-size: 22px; }
  nav { display: flex; gap: 8px; padding: 0 20px; }
  button, input { font: inherit; }
  button { background: #e8e8ed; border: 1px solid #d2d2d7; border-radius: 8px; padding: 6px 12px; }
  button.active, nav button.active { background: #fff; }
  button:disabled { color: #8e8e93; }
  main { display: grid; gap: 12px; }
  .card { background: #fff; border-radius: 12px; padding: 16px; }
  label { display: flex; gap: 8px; align-items: center; }
  input[type="text"] { flex: 1; border: 1px solid #d2d2d7; border-radius: 8px; padding: 6px 8px; }
  .row, .actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #e8e8ed; }
  tr.selected { background: #e8f0fe; }
  #log { background: #1e1e1e; color: #f5f5f7; min-height: 160px; white-space: pre-wrap; border-radius: 8px; padding: 12px; }
  .bar { height: 8px; background: #e8e8ed; border-radius: 4px; overflow: hidden; }
  .bar > div { height: 100%; width: 0; background: #0a84ff; }
  a { color: #0969da; }
  .hidden { display: none; }
  .update { color: #d70015; font-weight: 600; }
  td .actions { margin-top: 0; }
</style>
</head>
<body>
<header>
  <h1>CPA Mac 管理器</h1>
  <div><span id="proxy">代理：直连</span> <button id="settings-toggle" type="button">设置</button></div>
</header>
<nav>
  <button type="button" data-tab="cli" class="active">CLIProxyAPI</button>
  <button type="button" data-tab="plus">CPA-Manager-Plus</button>
  <button type="button" data-tab="portable">免安装软件</button>
  <button type="button" data-tab="package">安装包</button>
</nav>
<main>
  <section id="settings" class="card hidden">
    <label><input id="proxy-enabled" type="checkbox"> 使用 HTTP 代理</label>
    <div class="row"><input id="proxy-url" type="text"></div>
    <div class="row"><span>安装包下载目录</span><input id="download-dir" type="text"><button id="browse-download" type="button">选择</button></div>
    <div class="actions"><button id="save-settings" type="button">保存设置</button></div>
  </section>
  <section id="panel-cli" class="card"></section>
  <section id="panel-plus" class="card hidden"></section>
  <section id="panel-portable" class="card hidden"></section>
  <section id="panel-package" class="card hidden"></section>
  <div class="row"><span id="status">就绪</span><button id="cancel" type="button" class="hidden">取消</button></div>
  <div class="bar"><div id="progress"></div></div>
  <div id="log"></div>
</main>
<script>
let state = null;
let selected = {portable: "", package: ""};
const tabs = ["cli", "plus", "portable", "package"];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, body) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "操作失败");
  return data;
}

function projectCard(key) {
  const item = state.projects[key];
  const latest = esc(item.latest || "尚未检查") + (item.update ? ' <span class="update">可更新</span>' : "");
  const address = item.endpoint
    ? `<a href="${esc(item.endpoint)}" target="_blank">${esc(item.endpoint)}</a>`
    : "安装后可打开";
  return `<a href="${esc(item.repo)}" target="_blank">项目主页：${esc(item.repo)}</a>
    <div class="row"><span>安装目录</span><input id="dir-${key}" type="text" value="${esc(item.directory)}">
      <button type="button" data-browse="${key}">选择</button><button type="button" data-open="${esc(item.directory)}">打开</button></div>
    <p>本地版本：${esc(item.local || "未安装")}　　最新版本：${latest}</p>
    <p>附件：${esc(item.asset || "尚未检查")}　　服务：${esc(item.service)}</p>
    <p>页面：${address}</p>
    <p>首次安装会写入仅监听本机的配置，已有配置文件不会被覆盖。</p>
    <div class="actions">
      <button type="button" data-project="${key}" data-action="check">检查最新版</button>
      <button type="button" data-project="${key}" data-action="install">${item.installed ? "升级" : "安装最新版"}</button>
      <button type="button" data-project="${key}" data-action="start">启动</button>
      <button type="button" data-project="${key}" data-action="stop">停止</button>
      <button type="button" data-project="${key}" data-action="restart">重启</button>
      <button type="button" data-open-page="${key}">打开页面</button>
    </div>`;
}

function listed(mode, repo) {
  const rows = state[mode === "portable" ? "portable" : "packages"] || [];
  return rows.some(row => String(row.repository).toLowerCase() === String(repo).toLowerCase());
}

function catalogItems(mode, query) {
  const want = mode === "portable" ? "免安装" : "安装包";
  const text = (query || "").toLowerCase();
  return state.catalog.filter(item => item.mode === want && (!text || (item.name + " " + item.description + " " + item.repository).toLowerCase().includes(text)));
}

function libraryList(mode, query) {
  const items = catalogItems(mode, query);
  if (!items.length) return "<p>软件库没有匹配项。</p>";
  return items.map(item => {
    const button = listed(mode, item.repository)
      ? '<button type="button" disabled>已添加</button>'
      : `<button type="button" data-quick="${mode}" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">添加</button>`;
    return `<div class="row"><span>${esc(item.name)} ${esc(item.description)}</span>${button}</div>`;
  }).join("");
}

function openLibrary(mode) {
  const box = document.getElementById("library-" + mode);
  box.classList.remove("hidden");
  box.innerHTML = `<div class="row"><input id="lib-query-${mode}" data-lib-filter="${mode}" type="text" placeholder="筛选名称或说明">
      <button type="button" data-github-search="${mode}">搜索 GitHub</button></div>
    <div id="lib-list-${mode}"></div><div id="github-hits-${mode}"></div>`;
  document.getElementById("lib-list-" + mode).innerHTML = libraryList(mode, "");
}

function softwareCard(mode) {
  const rows = state[mode === "portable" ? "portable" : "packages"];
  const head = mode === "portable" ? "目录" : "仓库";
  const body = rows.map(row => {
    const latest = esc(row.latest || "未检查") + (row.update ? ' <span class="update">可更新</span>' : "");
    const place = mode === "portable" ? row.directory : row.repository;
    return `<tr data-mode="${mode}" data-id="${esc(row.id)}" class="${selected[mode] === row.id ? "selected" : ""}">
      <td>${esc(row.name)}</td><td>${latest}</td><td>${esc(row.local || (mode === "portable" ? "未安装" : "—"))}</td>
      <td>${esc(row.asset || "—")}</td><td>${esc(place)}</td>
      <td><div class="actions">
        <button type="button" data-row-check="${mode}" data-id="${esc(row.id)}">检查</button>
        <button type="button" data-row-install="${mode}" data-id="${esc(row.id)}">${mode === "portable" ? "安装" : "下载并打开"}</button>
      </div></td></tr>`;
  }).join("");
  const current = rows.find(row => row.id === selected[mode]);
  const editor = mode === "portable" && current
    ? `<div class="row"><span>所选安装目录</span><input id="edit-dir" data-edit-dir="${esc(current.id)}" type="text" value="${esc(current.directory)}"></div>`
    : "";
  const extra = mode === "portable"
    ? `<div class="row"><span>安装目录</span><input id="add-dir-${mode}" type="text"><button type="button" data-browse-add="${mode}">选择</button></div>
       <div class="row"><span>额外保留</span><input id="add-keep-${mode}" type="text" placeholder="; 分隔，可留空"></div>`
    : "";
  return `<div class="actions">
      <button type="button" data-library="${mode}">软件库</button>
      <button type="button" data-check-all="${mode}">全部检查</button>
      <button type="button" data-install="${mode}">${mode === "portable" ? "安装所选" : "下载并打开"}</button>
      <button type="button" data-remove="${mode}">移除</button>
      <button type="button" data-open-selected="${mode}">打开目录</button>
    </div>
    <div class="row"><span>名称</span><input id="add-name-${mode}" type="text"><span>GitHub</span><input id="add-repo-${mode}" type="text">
      <button type="button" data-add="${mode}">添加</button></div>
    ${extra}
    ${editor}
    <div id="library-${mode}" class="hidden"></div>
    <table><thead><tr><th>名称</th><th>最新版本</th><th>本地版本</th><th>附件</th><th>${head}</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`;
}

function writePanel(id, html) {
  const panel = document.getElementById(id);
  const active = document.activeElement;
  let keep = null;
  if (active && panel.contains(active) && active.id) {
    keep = {id: active.id, value: active.value, start: active.selectionStart, end: active.selectionEnd};
  }
  panel.innerHTML = html;
  if (!keep) return;
  const field = document.getElementById(keep.id);
  if (!field) return;
  field.value = keep.value;
  field.focus();
  try {
    if (keep.start != null) field.setSelectionRange(keep.start, keep.end);
  } catch (error) {}
}

function render() {
  document.getElementById("proxy").textContent = state.proxy_label;
  document.getElementById("proxy-enabled").checked = state.proxy_enabled;
  if (document.activeElement.id !== "proxy-url") document.getElementById("proxy-url").value = state.proxy;
  if (document.activeElement.id !== "download-dir") document.getElementById("download-dir").value = state.download_directory;
  for (const key of ["cli", "plus"]) writePanel("panel-" + key, projectCard(key));
  for (const mode of ["portable", "package"]) writePanel("panel-" + mode, softwareCard(mode));
  paintJob(state.job);
}

function paintJob(job) {
  const cancel = document.getElementById("cancel");
  if (!job) {
    cancel.classList.add("hidden");
    return;
  }
  document.getElementById("progress").style.width = (job.progress || 0) + "%";
  document.getElementById("log").textContent = (job.lines || []).join(String.fromCharCode(10));
  document.getElementById("status").textContent = job.error || (job.done ? "完成" : "正在执行…");
  cancel.classList.toggle("hidden", !!job.done);
}

async function refresh() {
  state = await (await fetch("/api/state")).json();
  render();
  if (state.job && !state.job.done) poll(state.job.id);
}

async function poll(id) {
  const job = await (await fetch("/api/job/" + id)).json();
  if (job.done !== true && job.done !== false) {
    document.getElementById("status").textContent = job.error || "任务不存在";
    return;
  }
  paintJob(job);
  if (!job.done) setTimeout(() => poll(id), 400);
  else refresh();
}

document.getElementById("settings-toggle").onclick = () => document.getElementById("settings").classList.toggle("hidden");
document.getElementById("save-settings").onclick = async () => {
  try {
    state = await api("/api/settings", {
      proxy_enabled: document.getElementById("proxy-enabled").checked,
      proxy: document.getElementById("proxy-url").value,
      download_directory: document.getElementById("download-dir").value
    });
    render();
    document.getElementById("status").textContent = "设置已保存";
  } catch (error) { alert(error.message); }
};
document.getElementById("browse-download").onclick = async () => {
  const data = await api("/api/choose-directory");
  if (data.path) document.getElementById("download-dir").value = data.path;
};
document.getElementById("cancel").onclick = async () => {
  try { await api("/api/cancel"); }
  catch (error) { alert(error.message); }
};

document.body.addEventListener("click", async event => {
  const tab = event.target.dataset.tab;
  if (tab) {
    for (const name of tabs) {
      document.getElementById("panel-" + name).classList.toggle("hidden", name !== tab);
      document.querySelector(`[data-tab="${name}"]`).classList.toggle("active", name === tab);
    }
    return;
  }
  const browse = event.target.dataset.browse;
  if (browse) {
    const data = await api("/api/choose-directory");
    if (!data.path) return;
    document.getElementById("dir-" + browse).value = data.path;
    state = await api("/api/project/" + browse + "/directory", {directory: data.path});
    render();
    return;
  }
  const browseAdd = event.target.dataset.browseAdd;
  if (browseAdd) {
    const data = await api("/api/choose-directory");
    if (data.path) document.getElementById("add-dir-" + browseAdd).value = data.path;
    return;
  }
  const open = event.target.dataset.open;
  if (open) { await api("/api/open", {path: open}); return; }
  const project = event.target.dataset.project;
  if (project) {
    const action = event.target.dataset.action;
    if (action === "install" || action === "check" || action === "start" || action === "stop" || action === "restart") {
      try {
        const directory = document.getElementById("dir-" + project).value;
        await api("/api/project/" + project + "/directory", {directory});
        const data = await api("/api/project/" + project + "/" + action);
        if (data.job_id) poll(data.job_id);
      } catch (error) { alert(error.message); }
    }
    return;
  }
  if (event.target.dataset.library) {
    const modeName = event.target.dataset.library;
    const box = document.getElementById("library-" + modeName);
    if (!box.classList.contains("hidden")) {
      box.classList.add("hidden");
      return;
    }
    openLibrary(modeName);
    return;
  }
  if (event.target.dataset.githubSearch) {
    const searchMode = event.target.dataset.githubSearch;
    const query = document.getElementById("lib-query-" + searchMode).value;
    try {
      const data = await api("/api/search", {query});
      const hits = data.results || [];
      const markup = hits.length ? hits.map(item => {
        const button = listed(searchMode, item.repository)
          ? '<button type="button" disabled>已添加</button>'
          : `<button type="button" data-quick="${searchMode}" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">添加</button>`;
        return `<div class="row"><span>${esc(item.full_name)} ${esc(item.description)}</span>${button}</div>`;
      }).join("") : "<p>没有匹配的 GitHub 仓库。</p>";
      document.getElementById("github-hits-" + searchMode).innerHTML = `<p>GitHub 约 ${data.total} 个结果，显示前 ${hits.length} 个。</p>` + markup;
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.quick) {
    const quickMode = event.target.dataset.quick;
    const directory = quickMode === "portable" ? state.apps_directory + "/" + event.target.dataset.name : "";
    try {
      state = await api("/api/software/" + quickMode + "/add", {
        name: event.target.dataset.name,
        repository: event.target.dataset.repo,
        directory,
        preserve: ""
      });
      render();
      openLibrary(quickMode);
    } catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.rowCheck || event.target.dataset.rowInstall) {
    const rowMode = event.target.dataset.rowCheck || event.target.dataset.rowInstall;
    const rowAction = event.target.dataset.rowCheck ? "check" : "install";
    selected[rowMode] = event.target.dataset.id;
    try {
      const data = await api("/api/software/" + rowMode + "/" + rowAction, {id: event.target.dataset.id});
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
    try { const data = await api("/api/software/" + event.target.dataset.checkAll + "/check", {all: true}); poll(data.job_id); }
    catch (error) { alert(error.message); }
    return;
  }
  if (event.target.dataset.install) {
    const id = selected[event.target.dataset.install];
    if (!id) return alert("请先选择一行。");
    try { const data = await api("/api/software/" + event.target.dataset.install + "/install", {id}); poll(data.job_id); }
    catch (error) { alert(error.message); }
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
</script>
</body>
</html>
"""


def acquire_instance():
    if fcntl is None:
        raise SystemExit("CPA Mac 管理器仅支持 macOS。")
    path = support_dir() / "instance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit("CPA Mac 管理器已在运行。请先退出旧窗口。")
    return handle


def main():
    handle = acquire_instance()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print("CPA Mac 管理器已打开：" + url, flush=True)
        subprocess.Popen(["open", url])
        server.serve_forever()
    finally:
        handle.close()
