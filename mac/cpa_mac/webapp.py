"""Local browser UI for the Mac manager."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from cpa_mac.backends import github, service, software
from cpa_mac.config import (
    CATALOG, MAC_ROOT, PROJECTS, SOURCE_VERSION, has_update, host_architecture,
    DEFAULT_DOWNLOAD_WORKERS, normalize_download_workers
)
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

    def download_workers(self):
        return normalize_download_workers(self.data.get("download_workers", DEFAULT_DOWNLOAD_WORKERS))

    def prerelease(self):
        return bool(self.data.get("prerelease", False))

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
            endpoint = f"http://{host}:{port}/management.html"
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
        "is_running": running.startswith("运行中"),
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
        "download_workers": store.download_workers(),
        "prerelease": store.prerelease(),
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


def check_project(store, key, report, force=False):
    spec = PROJECTS[key]
    root = store.data[key]["directory"]
    local, release, asset = service.check(spec, root, store.proxy(), host_architecture(), report,
                                          include_prerelease=store.prerelease(), force=force)
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
                                      report, MAC_ROOT, cancel,
                                      include_prerelease=store.prerelease(),
                                      workers=store.download_workers())
    store.data[key]["latest"] = tag
    store.data[key]["asset"] = asset_name
    store.save()
    refresh_local(store, key)


def find_record(store, mode, identity):
    records = store.data["portable" if mode == "portable" else "packages"]
    return next((item for item in records if item["id"] == identity), None)


def check_record(store, mode, record, report, force=False):
    cancelled(store)
    report(None, f"正在检查 {record['name']}…")
    release = github.fetch_latest(record["repository"], store.proxy(),
                                  include_prerelease=store.prerelease(), force=force)
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
            workers = body.get("download_workers")
            prerelease = body.get("prerelease")
            if enabled:
                from cpa_mac.core.network import network
                network(proxy)
            if not folder.strip():
                raise ValueError("请填写下载目录。")
            store.data["proxy_enabled"] = enabled
            store.data["proxy"] = proxy.strip() or "http://127.0.0.1:7890"
            store.data["download_directory"] = folder.strip()
            if workers is not None:
                store.data["download_workers"] = normalize_download_workers(workers)
            if prerelease is not None:
                store.data["prerelease"] = bool(prerelease)
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
            force = bool(body.get("force", False))
            return {"job_id": store.start(lambda report: check_project(store, key, report, force=force))}
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
                        check_record(store, mode, item, report, force=force)

                return {"job_id": store.start(work)}
            record = find_record(store, mode, body.get("id"))
            if record is None:
                raise ValueError("请先选择一行。")
            force = bool(body.get("force", False))
            return {"job_id": store.start(lambda report: check_record(store, mode, record, report, force=force))}
        if action == "install":
            record = find_record(store, mode, body.get("id"))
            if record is None:
                raise ValueError("请先选择一行。")

            def work(report):
                release, asset = check_record(store, mode, record, report)
                cancel = store.job.cancel if store.job else None
                if mode == "portable":
                    software.install_portable(release, asset, record["directory"], store.proxy(), report,
                                              MAC_ROOT, record.get("preserve") or "", cancel,
                                              workers=store.download_workers())
                else:
                    software.install_package(release, asset, store.data["download_directory"], store.proxy(), report, cancel,
                                            workers=store.download_workers())

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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CPA Mac 管理器</title>
<style>
  :root {
    --bg-app: #f5f6f8;
    --bg-card: #ffffff;
    --border: #e2e4e9;
    --border-light: #f0f1f4;
    --text-primary: #1d1d1f;
    --text-secondary: #6e6e73;
    --primary: #0071e3;
    --primary-hover: #0077ed;
    --success: #34c759;
    --success-hover: #30b753;
    --danger: #ff3b30;
    --tag-bg: #f2f3f5;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", Roboto, sans-serif; background: var(--bg-app); color: var(--text-primary); -webkit-font-smoothing: antialiased; }
  header { display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; background: #ffffff; border-bottom: 1px solid var(--border); }
  h1 { margin: 0; font-size: 19px; font-weight: 600; letter-spacing: -0.3px; }
  .header-actions { display: flex; align-items: center; gap: 12px; }
  .nav-wrap { padding: 14px 24px 2px; }
  nav { display: inline-flex; background: #e5e5ea; border-radius: 9px; padding: 3px; gap: 3px; }
  nav button { background: transparent; border: none; border-radius: 7px; padding: 6px 16px; font-weight: 500; color: #48484a; cursor: pointer; transition: all 0.16s ease; outline: none; }
  nav button:hover { color: #1d1d1f; background: rgba(255, 255, 255, 0.4); }
  nav button.active { background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 1px rgba(0,0,0,0.06); font-weight: 600; color: #000000; }
  button, input { font: inherit; }
  button { background: #f0f0f4; border: 1px solid var(--border); border-radius: 7px; padding: 6px 13px; cursor: pointer; color: var(--text-primary); transition: all 0.15s ease; outline: none; }
  button:hover:not(:disabled) { background: #e6e6ec; border-color: #d1d3d8; }
  button:disabled { opacity: 0.55; cursor: not-allowed; }
  button.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); font-weight: 500; }
  button.btn-primary:hover:not(:disabled) { background: var(--primary-hover); border-color: var(--primary-hover); }
  button.btn-success { background: var(--success); color: #fff; border-color: var(--success); font-weight: 500; }
  button.btn-success:hover:not(:disabled) { background: var(--success-hover); border-color: var(--success-hover); }
  button.btn-danger { background: #fff1f0; color: var(--danger); border-color: #ffccc7; }
  button.btn-danger:hover:not(:disabled) { background: #ffe4e6; }
  main { display: grid; gap: 14px; padding: 14px 24px 24px; }
  .card { background: var(--bg-card); border-radius: 12px; border: 1px solid var(--border); padding: 18px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
  label { display: inline-flex; gap: 8px; align-items: center; cursor: pointer; font-weight: 500; }
  input[type="text"], input[type="number"] { border: 1px solid var(--border); border-radius: 7px; padding: 6px 10px; outline: none; transition: border-color 0.15s ease; background: #fff; }
  input[type="text"]:focus, input[type="number"]:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.15); }
  .row, .actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
  .table-box { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: #fff; margin-top: 10px; }
  table { width: 100%; border-collapse: collapse; text-align: left; }
  th { background: #f8f9fa; color: var(--text-secondary); font-size: 13px; font-weight: 600; padding: 10px 12px; border-bottom: 1px solid var(--border); user-select: none; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border-light); font-size: 13.5px; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #fbfbfc; }
  tr.selected td { background: #edf4fe; }
  .red-dot { display: inline-block; width: 7px; height: 7px; background: var(--danger); border-radius: 50%; box-shadow: 0 0 0 2px rgba(255, 59, 48, 0.25); margin-right: 6px; vertical-align: middle; }
  .update-badge { display: inline-flex; align-items: center; background: #fff1f0; color: var(--danger); border: 1px solid #ffccc7; border-radius: 10px; padding: 1px 7px; font-size: 12px; font-weight: 600; margin-left: 6px; }
  .status-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: var(--tag-bg); color: var(--text-secondary); }
  .status-tag.active { background: #e6f4ea; color: #137333; font-weight: 500; }
  #log { background: #ffffff; color: #1d1d1f; border: 1px solid var(--border); border-radius: 9px; padding: 12px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "PingFang SC", monospace; font-size: 12.5px; line-height: 1.6; min-height: 150px; max-height: 260px; overflow-y: auto; white-space: pre-wrap; box-shadow: inset 0 1px 2px rgba(0,0,0,0.02); }
  .bar { height: 6px; background: #e5e5ea; border-radius: 3px; overflow: hidden; margin-top: 2px; }
  .bar > div { height: 100%; width: 0; background: var(--primary); transition: width 0.15s ease; }
  a { color: var(--primary); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .hidden { display: none !important; }
  .meta-text { color: var(--text-secondary); font-size: 13px; margin: 4px 0; }
  .settings-grid { display: grid; gap: 12px; margin-top: 10px; max-width: 720px; }
  .settings-item { display: flex; flex-direction: column; gap: 6px; }
  .settings-item span { font-weight: 500; font-size: 13px; color: var(--text-secondary); }
</style>
</head>
<body>
<header>
  <h1>CPA Mac 管理器</h1>
  <div class="header-actions">
    <span id="proxy-badge" class="status-tag">代理：直连</span>
    <button id="settings-toggle" type="button">设置</button>
  </div>
</header>
<div class="nav-wrap">
  <nav>
    <button type="button" data-tab="cli" class="active">CLIProxyAPI</button>
    <button type="button" data-tab="plus">CPA-Manager-Plus</button>
    <button type="button" data-tab="portable">免安装软件</button>
    <button type="button" data-tab="package">安装包</button>
  </nav>
</div>
<main>
  <section id="settings" class="card hidden">
    <h3 style="margin: 0 0 12px; font-size: 16px;">全局配置</h3>
    <div class="settings-grid">
      <label><input id="proxy-enabled" type="checkbox"> 启用 HTTP / HTTPS 代理</label>
      <div class="settings-item">
        <span>代理服务器地址</span>
        <input id="proxy-url" type="text" placeholder="http://127.0.0.1:7890">
      </div>
      <div class="settings-item">
        <span>分块下载并发数（1 ~ 16）</span>
        <div style="display: flex; gap: 8px; align-items: center;">
          <input id="download-workers" type="number" min="1" max="16" style="width: 100px;">
          <span style="font-size: 12px; color: var(--text-secondary);">多线程分块并发下载，支持断点续传（范围 1-16，默认 4）</span>
        </div>
      </div>
      <label><input id="prerelease-enabled" type="checkbox"> 检查并包含预发布版本（Pre-release）</label>
      <div class="settings-item">
        <span>安装包下载保存目录</span>
        <div style="display: flex; gap: 8px;">
          <input id="download-dir" type="text" style="flex: 1;">
          <button id="browse-download" type="button">选择目录</button>
        </div>
      </div>
      <div class="actions" style="margin-top: 14px;">
        <button id="save-settings" type="button" class="btn-primary">保存设置</button>
      </div>
    </div>
  </section>

  <section id="panel-cli" class="card"></section>
  <section id="panel-plus" class="card hidden"></section>
  <section id="panel-portable" class="card hidden"></section>
  <section id="panel-package" class="card hidden"></section>

  <div class="row" style="margin-top: 4px;">
    <span id="status" style="font-weight: 500; font-size: 13.5px;">就绪</span>
    <button id="cancel" type="button" class="btn-danger hidden">取消</button>
  </div>
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
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin: 12px 0; background: #f8f9fa; padding: 12px 14px; border-radius: 8px;">
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
  return state.catalog.filter(item => item.mode === want && (!text || (item.name + " " + item.description + " " + item.repository).toLowerCase().includes(text)));
}

function libraryList(mode, query) {
  const items = catalogItems(mode, query);
  if (!items.length) return "<p style='color: var(--text-secondary);'>软件库没有匹配项。</p>";
  return items.map(item => {
    const button = listed(mode, item.repository)
      ? '<button type="button" disabled>已添加</button>'
      : `<button type="button" class="btn-primary" data-quick="${mode}" data-name="${esc(item.name)}" data-repo="${esc(item.repository)}">添加</button>`;
    return `<div class="row" style="justify-content: space-between; border-bottom: 1px solid #f0f1f4; padding: 8px 0;">
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

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 16px;">
      <div style="background: #f8f9fa; border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
        <h4 style="margin: 0 0 10px; font-size: 14px;">添加新软件</h4>
        <div class="row"><span>名称：</span><input id="add-name-${mode}" type="text" placeholder="例如 ripgrep"></div>
        <div class="row"><span>仓库：</span><input id="add-repo-${mode}" type="text" placeholder="https://github.com/作者/仓库"></div>
        ${isPortable ? `
          <div class="row"><span>解压目录：</span><input id="add-dir-${mode}" type="text" placeholder="例如 ~/Applications/ripgrep"></div>
          <div class="row"><span>保留路径：</span><input id="add-keep-${mode}" type="text" placeholder="保留配置文件（选填）"></div>
        ` : ''}
        <div class="actions"><button type="button" class="btn-primary" data-add="${mode}">添加到列表</button></div>
      </div>

      <div style="background: #f8f9fa; border: 1px solid var(--border); border-radius: 9px; padding: 14px;">
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
  document.getElementById("proxy-badge").textContent = state.proxy_label;
  document.getElementById("proxy-enabled").checked = state.proxy_enabled;
  document.getElementById("proxy-url").value = state.proxy;
  document.getElementById("download-workers").value = state.download_workers || 4;
  document.getElementById("prerelease-enabled").checked = Boolean(state.prerelease);
  document.getElementById("download-dir").value = state.download_directory;

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
    const res = await fetch("/api/state");
    state = await res.json();
    render();
  } catch (e) {
    document.getElementById("status").textContent = "无法连接管理器：" + e.message;
  }
}

async function poll(jobId) {
  while (true) {
    try {
      const res = await fetch("/api/job/" + jobId);
      const job = await res.json();
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
</script>
</body>
</html>"""


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
        if getattr(sys, "stdout", None):
            print("CPA Mac 管理器已打开：" + url, flush=True)
        subprocess.Popen(["open", url])
        server.serve_forever()
    finally:
        handle.close()
