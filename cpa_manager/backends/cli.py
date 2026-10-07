"""CLIProxyAPI Windows manager. Dependencies: psutil, PyYAML."""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import socket
import secrets
import subprocess
import tempfile
import time
import uuid
import zipfile

import psutil
from cpa_manager.core.paths import ROOT
from cpa_manager.core.network import network as shared_network, read_text
from cpa_manager.core.locking import update_lock
from cpa_manager.core.files import reserved_name
from cpa_manager.core.runtime import architecture_compatible, discover_servers, windows_architecture
import yaml

REPO = "https://github.com/router-for-me/CLIProxyAPI"
PROTECTED = {"config.yaml", "update.py", "update.ps1", "update.bat",
             "start.bat", "stop.bat", "restart.bat", "update.lock", "update-settings.json",
             "cliproxyapi-manager.exe", "update-daily-check.json", "manager-service.log"}
DEFAULT_SETTINGS = {"proxy_enabled": False, "proxy": "http://127.0.0.1:7890", "server_dir": ""}


def load_settings(root=ROOT):
    path = root / "update-settings.json"
    if not path.exists():
        return DEFAULT_SETTINGS.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("proxy_enabled")) is not bool
            or not isinstance(data.get("proxy"), str)):
        raise ValueError("更新工具配置格式不正确：" + str(path))
    if not isinstance(data.get("server_dir", ""), str):
        raise ValueError("服务目录配置格式不正确。")
    return {key: data.get(key, default) for key, default in DEFAULT_SETTINGS.items()}


def save_settings(enabled, proxy, root=ROOT, validate=True, server_dir=None):
    try:
        data = load_settings(root)
    except (OSError, ValueError):
        data = DEFAULT_SETTINGS.copy()
    data.update(proxy_enabled=bool(enabled), proxy=proxy.strip())
    if server_dir is not None:
        data["server_dir"] = server_dir.strip()
    if enabled and validate:
        if not data["proxy"]:
            raise ValueError("启用代理时请填写代理地址。")
        network(data["proxy"])
    path = root / "update-settings.json"
    temporary = root / (".update-settings-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def daily_check_due(target, root=ROOT):
    try:
        data = json.loads((root / "update-daily-check.json").read_text(encoding="utf-8"))
        return data.get(str(target.resolve()).lower()) != time.strftime("%Y-%m-%d")
    except (OSError, ValueError, AttributeError):
        return True


def mark_daily_check(target, root=ROOT):
    path = root / "update-daily-check.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[str(target.resolve()).lower()] = time.strftime("%Y-%m-%d")
    temporary = root / (".update-daily-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def version_key(version):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", version.strip())
    if not match:
        return None
    prerelease = match[4]
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in prerelease.split(".")) if prerelease else ()
    return tuple(int(match[i]) for i in (1, 2, 3)), prerelease is None, identifiers


def local_version(root=ROOT):
    executable = root / "cli-proxy-api.exe"
    if not executable.is_file():
        return None
    try:
        result = subprocess.run([str(executable), "--help"], cwd=root, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"CLIProxyAPI Version:\s*([^,\s]+)", result.stdout + "\n" + result.stderr)
    return match[1] if match and version_key(match[1]) is not None else None


def already_current(local, latest):
    local_key = version_key(local) if local else None
    latest_key = version_key(latest)
    return local_key is not None and latest_key is not None and local_key >= latest_key


def network(proxy: str):
    return shared_network(proxy, user_agent="CLIProxyAPI-Updater/1.0")


def latest_release(opener, include_prerelease=False):
    endpoint = REPO + ("/releases" if include_prerelease else "/releases/latest")
    page = read_text(opener, endpoint)
    matches = re.findall(r'/router-for-me/CLIProxyAPI/releases/expanded_assets/([^"\s<>]+)', page)
    if not matches:
        match = re.search(r'/router-for-me/CLIProxyAPI/releases/expanded_assets/([^"\s<>]+)', page)
        matches = [match[1]] if match else []
    valid_tags = []
    seen = set()
    for t in matches:
        if t not in seen and version_key(t) is not None:
            seen.add(t)
            valid_tags.append(t)
    if include_prerelease:
        valid_tags.sort(key=version_key, reverse=True)
    if not valid_tags:
        if include_prerelease:
            return latest_release(opener, include_prerelease=False)
        raise RuntimeError("无法从 GitHub 发布页面识别最新版。")
    architecture = {"amd64": "amd64", "arm64": "aarch64"}[windows_architecture()]
    for tag in valid_tags:
        try:
            assets = read_text(opener, REPO + "/releases/expanded_assets/" + tag)
            links = {html.unescape(link) for link in re.findall(
                r'href="(/router-for-me/CLIProxyAPI/releases/download/[^"<>]+)"', assets)}
            packages = [link for link in links if re.search(
                rf"/CLIProxyAPI_[^/]+_windows_{architecture}\.zip$", link)]
            sums = [link for link in links if link.endswith("/checksums.txt")]
            if len(packages) == 1 and len(sums) == 1:
                return tag, "https://github.com" + packages[0], "https://github.com" + sums[0]
        except Exception:
            continue
    if include_prerelease:
        return latest_release(opener, include_prerelease=False)
    raise RuntimeError(f"发布页面缺少唯一的 Windows {architecture} ZIP 包或 checksums.txt。")


def download(opener, url, destination, report):
    digest = hashlib.sha256()
    with opener.open(url, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        received, last_report = 0, 0.0
        while chunk := response.read(256 * 1024):
            output.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            now = time.monotonic()
            if now - last_report >= 0.1:
                size = f"{received / 1048576:.1f} MB"
                if total:
                    size += f" / {total / 1048576:.1f} MB"
                report(10 + 60 * received / total if total else None, "正在下载：" + size)
                last_report = now
        if total and received != total:
            raise RuntimeError("下载不完整，请重试。")
    return digest.hexdigest()


def extract_package(archive: Path, stage: Path):
    seen = set()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = info.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            if (not parts or any(p in ("", ".", "..") or ":" in p or p.endswith((".", " ")) for p in parts)
                    or PureWindowsPath(name).is_absolute() or PureWindowsPath(name).drive
                    or any(reserved_name(p) for p in parts)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeError("压缩包包含不安全的路径：" + name)
            key = name.rstrip("/").lower()
            if key in seen:
                raise RuntimeError("压缩包包含重复路径：" + name)
            seen.add(key)
            target = stage.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    executables = list(stage.rglob("cli-proxy-api.exe"))
    if len(executables) != 1:
        raise RuntimeError("安装包中找不到唯一的 cli-proxy-api.exe。")
    return executables[0].parent


def powershell(script, **env):
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); " + script],
        env={**os.environ, **env}, capture_output=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    if result.returncode:
        raise RuntimeError("程序管理失败：" + result.stderr.strip())
    return result.stdout.strip()


def running_servers(root):
    return discover_servers(root, "cli-proxy-api.exe", include_environment=False)


def stop_server(root, server):
    powershell(
        "$p=Get-Process -Id ([int]$env:UPDATER_PID) -ErrorAction SilentlyContinue; "
        "if ($p -and $p.Path -eq $env:UPDATER_EXE) {"
        "$p.Kill(); if (-not $p.WaitForExit(30000)) {throw 'Stop timed out'}}",
        UPDATER_EXE=str(root / "cli-proxy-api.exe"), UPDATER_PID=str(server["ProcessId"]))


def server_endpoint(root, server=None):
    config = root / "config.yaml"
    command = (server or {}).get("CommandLine")
    if command:
        import ctypes
        from ctypes import wintypes
        split = ctypes.windll.shell32.CommandLineToArgvW
        split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        split.restype = ctypes.POINTER(wintypes.LPWSTR)
        count = ctypes.c_int()
        pointer = split(command, ctypes.byref(count))
        if not pointer:
            raise RuntimeError("无法解析原程序的配置参数。")
        try:
            arguments = [pointer[i] for i in range(count.value)]
        finally:
            free = ctypes.windll.kernel32.LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            free(pointer)
        for index, argument in enumerate(arguments):
            if argument in ("--config", "-config") and index + 1 < len(arguments):
                config = Path(arguments[index + 1])
            elif argument.startswith(("--config=", "-config=")):
                config = Path(argument.split("=", 1)[1])
        if not config.is_absolute():
            config = root / config
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as error:
        # YAML exceptions can contain secrets from nearby configuration lines.
        raise RuntimeError(f"无法读取服务配置：{config}（{type(error).__name__}）") from None
    if not isinstance(data, dict):
        raise RuntimeError("服务配置必须是 YAML 对象。")
    listener = data.get("server", data)
    if not isinstance(listener, dict):
        raise RuntimeError("server 配置必须是对象。")
    host, port = listener.get("host", ""), listener.get("port", 8317)
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("配置中的 host 必须是字符串，port 必须是 1～65535 的整数。")
    return host, port


def listening_sockets():
    return [connection for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN]


def port_owners(port):
    owners = []
    for connection in listening_sockets():
        if connection.laddr.port != port:
            continue
        pid = connection.pid
        try:
            name = psutil.Process(pid).name() if pid else "未知程序"
        except psutil.Error:
            name = "未知程序"
        owners.append(f"{name}（PID {pid}，{connection.laddr.ip}:{port}）")
    return "；".join(sorted(set(owners)))


def ensure_port_available(host, port):
    # SO_EXCLUSIVEADDRUSE prevents Windows from accepting an overlapping bind.
    addresses = socket.getaddrinfo(host or "::", port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    sockets = []
    seen = set()
    try:
        for family, kind, protocol, _, address in addresses:
            if (family, address) in seen:
                continue
            seen.add((family, address))
            probe = socket.socket(family, kind, protocol)
            sockets.append(probe)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            try:
                probe.bind(address)
            except OSError as error:
                try:
                    owners = port_owners(port)
                except psutil.Error:
                    owners = ""
                if owners:
                    raise RuntimeError(f"无法启动：端口 {port} 已被占用：{owners}。请先释放端口或修改 config.yaml。") from None
                raise RuntimeError(f"无法绑定 {host or '*'}:{port}，端口可能被占用、被系统保留或地址不可用（错误 {error.winerror}）。") from None
    finally:
        for probe in sockets:
            probe.close()


def process_listens(pid, host, port, listeners):
    addresses = {entry[4][0] for entry in socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)} if host else {"0.0.0.0", "::"}
    return any(connection.pid == pid and connection.laddr.port == port
               and connection.laddr.ip in addresses for connection in listeners)


def server_status(root, servers):
    listeners = listening_sockets()
    if not servers:
        try:
            _, port = server_endpoint(root)
            occupied = any(connection.laddr.port == port for connection in listeners)
        except RuntimeError:
            occupied = False
        return False, "未运行" + (f"（端口 {port} 被其他进程占用）" if occupied else "")
    for server in servers:
        try:
            host, port = server_endpoint(root, server)
            if process_listens(server["ProcessId"], host, port, listeners):
                return True, f"运行中（PID {server['ProcessId']}，端口 {port}）"
        except (RuntimeError, OSError):
            pass
    return True, "异常：进程存在，但未监听配置端口"


def restart_server(root, server, startup_timeout=20):
    host, port = server_endpoint(root, server)
    ensure_port_available(host, port)
    # Preserve the original arguments (including a custom --config path).
    command = server.get("CommandLine") or [str(root / "cli-proxy-api.exe"), "--config", "config.yaml"]
    log_path = root / "manager-service.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, executable=str(root / "cli-proxy-api.exe"), cwd=root,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + startup_timeout
        ready_since = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"程序启动失败，退出码 {process.returncode}。日志：{log_path}")
            if process_listens(process.pid, host, port, listening_sockets()):
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= 1:
                    return
            else:
                ready_since = None
            time.sleep(0.25)
        raise RuntimeError(f"启动失败：{startup_timeout} 秒内未确认进程 PID {process.pid} 监听端口 {port}。日志：{log_path}")
    except Exception:
        # Only clean up the child launched by this attempt, never the port owner.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise


def control_server(action, report, root=ROOT):
    if action not in ("start", "stop", "restart"):
        raise ValueError("未知的程序操作。")
    with update_lock(root):
        servers = running_servers(root)
        if action == "start" and servers:
            _, state = server_status(root, servers)
            if state.startswith("异常"):
                raise RuntimeError(state + "。请停止异常进程后重新启动，并检查端口和日志。")
            report(100, "程序已在运行，无需重复启动。")
            return
        if action in ("start", "restart"):
            if not (root / "cli-proxy-api.exe").is_file():
                raise RuntimeError("找不到 cli-proxy-api.exe，请先更新安装。")
            if not (root / "config.yaml").is_file():
                raise RuntimeError("找不到 config.yaml，请先配置服务。")
        if action in ("stop", "restart"):
            for server in servers:
                report(None, "正在停止当前目录的程序…")
                stop_server(root, server)
            if action == "stop":
                report(100, "程序已停止。" if servers else "程序当前未运行。")
                return
        report(None, "正在启动程序…")
        # Match start.bat/restart.bat: start hidden with the local config.yaml.
        restart_server(root, {})
        report(100, "程序已重启。" if action == "restart" else "程序已启动，可关闭本工具。")


def initialize_config(root):
    config = root / "config.yaml"
    if not config.exists():
        with config.open("x", encoding="utf-8") as output:
            yaml.safe_dump({"host": "127.0.0.1", "port": 8317, "auth-dir": "./auth",
                            "api-keys": [secrets.token_urlsafe(32)]}, output, allow_unicode=True)


def install(stage, root, report):
    files = [path.relative_to(stage) for path in stage.rglob("*") if path.is_file()
             and path.relative_to(stage).as_posix().lower() not in PROTECTED
             and (path.relative_to(stage).parts[0].lower() in {"docs", "static"}
                  or path.relative_to(stage).as_posix().lower() in {
                      "cli-proxy-api.exe", "config.example.yaml", "readme.md", "readme_cn.md", "license"})]
    # Do not follow local symlinks/junctions outside the chosen directory.
    for relative in files:
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("目标文件指向更新目录之外：" + str(relative))
    backup = root / ".update-backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    report(80, "正在备份旧文件…")
    for relative in [*files, Path("config.yaml")]:
        source = root / relative
        if source.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    report(84, "备份位置：" + str(backup))
    stopped, changed = [], []
    try:
        for server in running_servers(root):
            report(85, "正在停止当前目录的程序…")
            stop_server(root, server)
            stopped.append(server)
        for index, relative in enumerate(files):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            changed.append(relative)
            shutil.copy2(stage / relative, target)
            report(86 + 10 * (index + 1) / len(files), "正在安装：" + str(relative))
        if not (root / "config.yaml").exists():
            changed.append(Path("config.yaml"))
            initialize_config(root)
            report(97, "首次安装配置已创建，仅监听本机。")
    except Exception as failure:
        errors = []
        for relative in reversed(changed):
            try:
                if (backup / relative).is_file():
                    shutil.copy2(backup / relative, root / relative)
                else:
                    (root / relative).unlink(missing_ok=True)
            except Exception as error:
                errors.append(str(error))
        if not errors:
            for server in stopped:
                try:
                    restart_server(root, server)
                except Exception as error:
                    errors.append(str(error))
        detail = "；已恢复旧文件。" if not errors else "；恢复或重启失败：" + "; ".join(errors)
        raise RuntimeError(f"{failure}{detail}\n备份目录：{backup}") from failure
    for server in stopped:
        report(98, "正在重启程序…")
        try:
            restart_server(root, server)
        except Exception as error:
            raise RuntimeError(f"文件已更新，但自动重启失败：{error}\n备份目录：{backup}") from error
    return backup


def update(proxy, report, check_only=False, root=ROOT, versions=None, include_prerelease=False):
    opener = network(proxy)
    local = local_version(root)
    report(None, "本地版本：" + (local or "未安装或无法识别"))
    if versions:
        versions(local, None)
    report(None, "正在检查 GitHub 最新版本…")
    tag, url, checksum_url = latest_release(opener, include_prerelease=include_prerelease)
    if versions:
        versions(local, tag)
    report(5, "最新版：" + tag)
    if already_current(local, tag) and architecture_compatible(root / "cli-proxy-api.exe"):
        report(100, f"无需更新：本地 {local} 已是最新版或高于发布版 {tag}，已跳过下载和安装。")
        return tag
    if check_only:
        report(100, f"检测完成：本地 {local or '未知'}，最新版 {tag}。" + ("可更新。" if local else "可安装最新版。"))
        return tag
    with update_lock(root), tempfile.TemporaryDirectory(prefix="CLIProxyAPI-update-") as temporary:
        # Another updater may have completed while we were checking GitHub.
        local = local_version(root)
        if already_current(local, tag) and architecture_compatible(root / "cli-proxy-api.exe"):
            if versions:
                versions(local, tag)
            report(100, f"无需更新：本地已是 {local}，已跳过下载和安装。")
            return tag
        temp = Path(temporary)
        sums = read_text(opener, checksum_url)
        name = url.rsplit("/", 1)[-1]
        match = re.search(r"(?im)^([a-f0-9]{64})\s+\*?(?:\./)?" + re.escape(name) + r"\s*$", sums)
        if not match:
            raise RuntimeError("校验文件中找不到安装包的 SHA256。")
        archive = temp / "package.zip"
        actual = download(opener, url, archive, report)
        report(72, "正在校验 SHA256…")
        if actual.lower() != match[1].lower():
            raise RuntimeError("SHA256 校验失败，本地程序尚未修改。")
        report(75, "正在解压安装包…")
        stage = temp / "extracted"
        stage = extract_package(archive, stage)
        backup = install(stage, root, report)
        if versions:
            versions(local_version(root), tag)
        report(100, f"更新完成：{tag}；配置已保留。备份：{backup}")
    return tag
