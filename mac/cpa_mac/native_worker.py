"""JSON-lines worker for the native window; no HTTP server or browser."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import psutil

from cpa_mac.backends import github, release_selection, service, software
from cpa_mac.config import PROJECTS, SOURCE_VERSION, has_update, host_architecture
from cpa_mac.core.macos import system_doctor

OUTPUT_LOCK = threading.Lock()


def emit(event):
    with OUTPUT_LOCK:
        print(json.dumps(event, ensure_ascii=False), flush=True)


def safe_root(directory, source):
    if not directory or not Path(directory).expanduser().is_absolute():
        raise ValueError("请指定独立的绝对安装目录。")
    root = service.assert_install_directory(directory, source)
    if root == Path.home().resolve() or root in Path(source).resolve().parents:
        raise ValueError("不能操作家目录或管理器的上级目录。")
    return root


def asset_path(folder, name):
    if not name or Path(name).name != name or name in (".", "..") or "\\" in name:
        raise ValueError("附件名称无效，请先检查版本。")
    target = Path(folder).expanduser().resolve() / name
    if target.is_symlink():
        raise ValueError("不能操作符号链接下载包。")
    return target


def clean_downloads(entries, folder, report):
    root = Path(folder).expanduser().resolve()
    removed = []
    deleted = 0
    for entry in entries:
        target = Path(entry["path"])
        if target.parent != root or target.is_symlink():
            continue
        if not target.exists():
            removed.append(str(target))
        elif target.is_file() and github.file_sha256(target) == entry["sha256"]:
            target.unlink()
            removed.append(str(target))
            deleted += 1
        else:
            report(None, f"保留已变更的文件：{target.name}")
    return {"deleted": deleted, "removed_paths": removed}


def trash_app(record, source):
    path = Path(record.get("installed_dir") or "").expanduser()
    roots = [Path("/Applications"), Path.home() / "Applications"]
    if path.suffix != ".app" or path.is_symlink() or not path.is_dir():
        raise ValueError("请先关联可卸载的 .app 应用；PKG 安装的软件需按其官方说明卸载。")
    path = path.resolve()
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("只允许将用户 Applications 中的应用移至废纸篓。")
    if path == Path(source).resolve() or Path(source).resolve().is_relative_to(path):
        raise ValueError("不能卸载正在使用的管理器。")
    for process in psutil.process_iter():
        try:
            if Path(process.exe()).is_relative_to(path):
                raise ValueError("软件仍在运行，请正常退出后再卸载。")
        except (psutil.Error, OSError):
            continue
    script = 'tell application "Finder" to delete POSIX file ' + json.dumps(str(path), ensure_ascii=False)
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True)
    return {"installed_id": "", "installed_name": "", "installed_version": "", "installed_dir": "",
            "local": "", "installed_auto_disabled": True, "update": bool(record.get("latest"))}


def project_status(config):
    projects = {}
    for key, spec in PROJECTS.items():
        if key == "cli":
            brew_missing = not service.is_brew_installed()
            installed = service.is_cliproxyapi_installed()
            status, is_running = service.cliproxyapi_service_status()
            port_info = service.cliproxyapi_port_conflict()
            local = service.cliproxyapi_local_version() or ""
            value = dict(config.get("project_status", {}).get(key, {}))
            value.update(local=local, installed=installed, service=status,
                         is_running=is_running, backup_count=0,
                         brew_missing=brew_missing, directory="",
                         port_info=port_info)
            value["update"] = bool(value.get("latest")) and (not local or has_update(local, value["latest"]))
            brew_conf, home_conf = service.cliproxyapi_paths()
            cfg_path = home_conf if home_conf.exists() else brew_conf
            if cfg_path.is_file():
                try:
                    host, port = service.read_endpoint(cfg_path.parent, spec)
                    value["endpoint"] = f"http://{host}:{port}/management.html"
                except Exception:
                    value["endpoint"] = "http://127.0.0.1:8317/management.html"
            else:
                value["endpoint"] = "http://127.0.0.1:8317/management.html"
            projects[key] = value
        else:
            root = Path(config.get(key + "_directory", "")).expanduser().resolve()
            status = service.service_status(root, spec)
            value = dict(config.get("project_status", {}).get(key, {}))
            local = service.local_version(root, spec) or ""
            value.update(local=local, installed=(root / spec["binary"]).is_file(), service=status,
                         is_running=status.startswith("运行中"), backup_count=service.backup_count(root))
            value["update"] = bool(value.get("latest")) and (not local or has_update(local, value["latest"]))
            if (root / spec["config"]).is_file():
                try:
                    host, port = service.read_endpoint(root, spec)
                    value["endpoint"] = f"http://{host}:{port}/management.html"
                except RuntimeError as error:
                    value["endpoint"] = ""
                    value["service"] += "；" + str(error)
            if key == "plus":
                value.update(service.admin_key_status(root))
            projects[key] = value
    return projects


def execute(request, output=emit, cancel=None):
    config = request["config"]
    scope, action = request["scope"], request["action"]
    body = request.get("body", {})
    source = request["source_root"]
    proxy = config["proxy"] if config["proxy_enabled"] else ""
    prerelease = config["prerelease"]
    workers = config["download_workers"]
    cancel = cancel or threading.Event()

    def report(progress, line):
        output({"type": "progress", "progress": progress, "line": line})

    def downloaded(path):
        output({"type": "download", "path": str(path), "sha256": github.file_sha256(path)})

    if scope == "system":
        if action == "version":
            return {"version": SOURCE_VERSION}
        if action == "status":
            result = project_status(config)
            result["software"] = {}
            for mode in ("portable", "packages"):
                for record in config[mode]:
                    local = software.installed_tag(Path(record["directory"]).expanduser()) if mode == "portable" else record["local"]
                    target_tag = record.get("selected_tag") or record.get("latest")
                    if not target_tag:
                        try:
                            cached = release_selection.get_release_cache().get(record["repository"], include_prerelease=config.get("prerelease", False))
                            if cached:
                                target_tag = cached[0]["tag"]
                        except Exception:
                            pass
                    result["software"][record["id"]] = {"local": local, "update": bool(target_tag) and (not local or has_update(local, target_tag))}
            return result
        if action == "doctor":
            return system_doctor()
        if action == "manager_update":
            release = github.fetch_latest("Xunzi229/CPA-Win-Manager", proxy, force=body.get("force", False))
            return {"current": SOURCE_VERSION, "latest": release["tag"].removeprefix("v"),
                    "has_update": has_update(SOURCE_VERSION, release["tag"]),
                    "url": "https://github.com/Xunzi229/CPA-Win-Manager/releases"}
        raise ValueError("未知的系统操作。")

    if scope == "project":
        key = request["key"]
        spec = PROJECTS[key]
        if key == "cli":
            cli_dir = config.get("cli_directory", "")
            if action in ("check", "install"):
                brew_missing = not service.is_brew_installed()
                if action == "install":
                    service.install_cliproxyapi_brew(report, cancel)
                local = service.cliproxyapi_local_version() or ""
                latest = github.fetch_latest(spec["repo"], proxy, force=body.get("force", False))
                status, is_running = service.cliproxyapi_service_status()
                port_info = service.cliproxyapi_port_conflict()
                value = {
                    "local": local,
                    "latest": latest["tag"] if latest else "",
                    "asset": "",
                    "update": bool(latest) and (not local or has_update(local, latest["tag"])),
                    "brew_missing": brew_missing,
                    "installed": service.is_cliproxyapi_installed(),
                    "service": status,
                    "is_running": is_running,
                    "port_info": port_info,
                }
                output({"type": "project", "key": key, "directory": cli_dir, "value": value})
            elif action == "update":
                service.update_cliproxyapi_brew(report, cancel)
                local = service.cliproxyapi_local_version() or ""
                latest = github.fetch_latest(spec["repo"], proxy, force=False)
                status, is_running = service.cliproxyapi_service_status()
                port_info = service.cliproxyapi_port_conflict()
                value = {
                    "local": local,
                    "latest": latest["tag"] if latest else "",
                    "asset": "",
                    "update": bool(latest) and (not local or has_update(local, latest["tag"])),
                    "installed": True,
                    "service": status,
                    "is_running": is_running,
                    "port_info": port_info,
                }
                output({"type": "project", "key": key, "directory": cli_dir, "value": value})
            elif action in ("start", "stop", "restart"):
                service.control_cliproxyapi_brew(action, report)
                status, is_running = service.cliproxyapi_service_status()
                port_info = service.cliproxyapi_port_conflict()
                output({"type": "project", "key": key, "directory": cli_dir, "value": {"service": status, "is_running": is_running, "port_info": port_info}})
            elif action == "management_url":
                return {"url": service.management_url(Path.home(), spec)}
            elif action == "clear_backups":
                return {"deleted": 0}
            elif action in ("open_config", "open_log"):
                brew_conf, home_conf = service.cliproxyapi_paths()
                if action == "open_config":
                    target = home_conf if home_conf.exists() else brew_conf
                    if not target.exists():
                        target = home_conf
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("server:\n  host: 127.0.0.1\n  port: 8317\n", encoding="utf-8")
                else:
                    prefix = service.get_brew_prefix()
                    target = Path(prefix) / "var" / "log" / "cliproxyapi.log"
                    if not target.exists():
                        target = Path(prefix) / "var" / "log"
                subprocess.run(["open", str(target)], check=True)
            else:
                raise ValueError("不支持的服务操作：" + action)
            return {"ok": True}

        root = safe_root(config[key + "_directory"], source)
        if action in ("check", "install"):
            if action == "install":
                service.install(spec, root, proxy, host_architecture(), report, source, cancel,
                                include_prerelease=prerelease, workers=workers)
            local, release, asset = service.check(spec, root, proxy, host_architecture(), report,
                                                  include_prerelease=prerelease, force=body.get("force", False))
            value = {"local": local or "", "latest": release["tag"], "asset": asset["name"],
                     "update": not local or has_update(local, release["tag"])}
            output({"type": "project", "key": key, "directory": str(root), "value": value})
        elif action in ("start", "stop", "restart"):
            service.control(action, root, spec, report)
        elif action == "management_url":
            return {"url": service.management_url(root, spec)}
        elif action == "save_key":
            if key != "plus":
                raise ValueError("仅 CPA-Manager-Plus 使用管理员登录 Key。")
            service.save_admin_key(root, body.get("key", ""))
            return {**service.admin_key_status(root), "directory": config["plus_directory"]}
        elif action == "read_key":
            if key != "plus":
                raise ValueError("仅 CPA-Manager-Plus 使用管理员登录 Key。")
            return {**service.admin_key_status(root), "directory": config["plus_directory"]}
        elif action == "clear_backups":
            return {"deleted": service.clear_backups(root, report)}
        elif action in ("open_config", "open_log"):
            path = root / (spec["config"] if action == "open_config" else "manager-service.log")
            if not path.is_file():
                raise FileNotFoundError("文件不存在，请先安装或启动服务。")
            subprocess.run(["open", str(path)], check=True)
        else:
            raise ValueError("不支持的服务操作：" + action)
        return {"ok": True}

    mode = request["mode"]
    records = config["portable" if mode == "portable" else "packages"]
    if action in ("release_options", "release_assets", "select_release"):
        record = next((item for item in records if item["id"] == body.get("id")), None)
        if record is None:
            raise ValueError("软件记录不存在。")
        if body.get("repository") is not None and body["repository"] != record["repository"]:
            raise ValueError("仓库已修改，请重新打开版本选择。")
        if action == "release_options":
            page = body.get("page", 1)
            if page == 1 and not body.get("force"):
                cached = release_selection.get_release_cache().get(record["repository"], include_prerelease=prerelease)
                if cached:
                    return {"releases": [{"tag": item["tag"], "prerelease": item.get("prerelease", False)} for item in cached],
                            "page": 1, "has_more": len(cached) >= 30}
            return release_selection.list_releases(record["repository"], proxy, prerelease, page)
        if action == "release_assets":
            target_tag = body.get("tag", "")
            cached = release_selection.get_release_cache().get(record["repository"], include_prerelease=prerelease)
            release = None
            for item in cached:
                if (not target_tag and item.get("tag")) or item.get("tag") == target_tag:
                    release = item
                    break
            if release is None:
                release = release_selection.fetch_release(record["repository"], target_tag, proxy, prerelease)
            assets = release_selection.compatible_assets(release, mode, host_architecture())
            recommended = github.recommend(assets, mode, host_architecture())
            return {"tag": release["tag"], "assets": [{"name": item["name"], "size": item.get("size")} for item in assets],
                    "recommended": recommended["name"] if recommended else ""}
        value = release_selection.selection(record, mode, host_architecture(), body, proxy, prerelease)
        output({"type": "record", "mode": mode, "id": record["id"], "repository": record["repository"], "value": value})
        return {"ok": True}
    if action == "clean_downloads":
        return clean_downloads(config.get("downloaded_files", []), config["download_directory"], report)
    chosen = records if body.get("all") else [
        record for record in records if record["id"] == body.get("id")]
    if action == "upgrade_all":
        chosen = []
        for record in records:
            local = software.installed_tag(Path(record["directory"]).expanduser()) if mode == "portable" else record.get("local", "")
            target_tag = record.get("selected_tag") or record.get("latest")
            if target_tag and (not local or has_update(local, target_tag)):
                chosen.append(record)
    if not chosen:
        raise ValueError("没有可操作的软件记录，请先添加或选择软件。")
    errors = []
    for record in chosen:
        if cancel.is_set():
            raise RuntimeError("任务已取消。")
        try:
            if action in ("check", "install", "upgrade_all"):
                latest = github.fetch_latest(record["repository"], proxy, include_prerelease=prerelease,
                                             force=body.get("force", False)) if action == "check" else None
                if action == "check":
                    try:
                        if hasattr(github.fetch_latest, "mock_calls"):
                            if latest:
                                release_selection.get_release_cache().put(record["repository"], [latest], include_prerelease=prerelease)
                                release_selection.get_release_cache().flush()
                        else:
                            release_selection.fetch_catalog(
                                record["repository"], proxy, include_prerelease=prerelease,
                                force=body.get("force", False))
                    except Exception as error:
                        report(None, f"{record['name']}：拉取历史版本提示：{error}")
                record_for_resolve = dict(record)
                if action == "check":
                    record_for_resolve["selected_tag"] = ""
                    record_for_resolve["selected_asset"] = ""
                release, asset = release_selection.resolve(record_for_resolve, mode, host_architecture(), proxy, prerelease,
                                                            force=body.get("force", False), latest=latest)
                local = software.installed_tag(Path(record["directory"]).expanduser()) if mode == "portable" else record.get("local", "")
                latest_tag = latest["tag"] if latest else release["tag"]
                target_tag = latest_tag if action == "check" else (record.get("selected_tag") or latest_tag)
                value = {"latest": latest_tag, "asset": asset["name"], "local": local,
                         "update": not local or has_update(local, target_tag)}
                if action == "check":
                    value["selected_tag"] = ""
                    value["selected_asset"] = ""
                output({"type": "record", "mode": mode, "id": record["id"], "repository": record["repository"], "value": value})
                if not asset:
                    raise ValueError("该发布没有适合当前 Mac 架构的附件。")
                report(5, f"{record['name']}：{release['tag']} / {asset['name']}")
                if action == "check" or (action == "upgrade_all" and local and not has_update(local, release["tag"])):
                    continue
                if cancel.is_set():
                    raise RuntimeError("任务已取消。")
                if mode == "portable":
                    root = safe_root(record["directory"], source)
                    value["local"] = software.install_portable(release, asset, root, proxy, report, source,
                                                               record["preserve"], cancel, workers)
                    value["update"] = False
                    output({"type": "record", "mode": mode, "id": record["id"], "repository": record["repository"], "value": value})
                else:
                    asset_path(config["download_directory"], asset["name"])
                    software.install_package(release, asset, config["download_directory"], proxy, report,
                                             cancel, workers, open_installer=action == "install", downloaded=downloaded)
            elif action == "clear_backups":
                root = safe_root(record["directory"], source)
                deleted = 0
                for backup in root.glob(".install-backup-*"):
                    import re
                    if not backup.is_symlink() and backup.is_dir() and re.fullmatch(r"\.install-backup-[0-9a-f]{32}", backup.name):
                        shutil.rmtree(backup)
                        deleted += 1
                return {"deleted": deleted}
            elif action == "uninstall" and mode == "package":
                value = trash_app(record, source)
                output({"type": "record", "mode": mode, "id": record["id"], "repository": record["repository"], "value": value})
            else:
                raise ValueError("不支持的软件操作：" + action)
        except Exception as error:
            if len(chosen) == 1:
                raise
            detail = record["name"] + "：" + str(error)
            errors.append(detail)
            report(None, detail)
    if errors:
        raise RuntimeError("部分操作失败：\n" + "\n".join(errors))
    return {"ok": True}


def main():
    if getattr(sys, "frozen", False):
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    request = json.loads(sys.stdin.readline())
    cancelled = threading.Event()

    def read_cancel():
        for line in sys.stdin:
            if line.strip() == "cancel":
                cancelled.set()

    threading.Thread(target=read_cancel, daemon=True).start()
    try:
        result = execute(request, cancel=cancelled)
        emit({"type": "done", "result": result})
    except Exception as error:
        emit({"type": "done", "error": str(error)})


if __name__ == "__main__":
    main()
