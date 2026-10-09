"""Install, update and control CLIProxyAPI and CPA-Manager-Plus on macOS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

import psutil
import yaml

from cpa_mac.backends import github
from cpa_mac.config import already_current
from cpa_mac.core.archive import extract_archive
from cpa_mac.core.lock import file_lock


PROTECTED = {"config.yaml", "config.json", "manager-service.log", "update.lock"}


def assert_install_directory(path, source_root):
    root = Path(path).expanduser().resolve()
    source = Path(source_root).resolve()
    if root == Path(root.anchor) or root == source or source in root.parents:
        raise ValueError("请使用独立的软件目录，不能安装到磁盘根目录或管理器目录。")
    return root


def running_processes(root, binary_name):
    expected = Path(root) / binary_name
    if not expected.is_file():
        return []
    found = []
    for process in psutil.process_iter(["pid"]):
        try:
            exe = process.exe()
            if exe and os.path.samefile(exe, expected):
                found.append(process)
        except (psutil.Error, OSError, ValueError):
            continue
    return found


def service_status(root, spec):
    if not (Path(root) / spec["binary"]).is_file():
        return "未安装"
    processes = running_processes(root, spec["binary"])
    if not processes:
        return "未运行"
    return "运行中（PID " + ", ".join(str(process.pid) for process in processes) + "）"


def stop_processes(processes):
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(processes, timeout=15)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            continue
    psutil.wait_procs(alive, timeout=5)


def local_version(root, spec):
    binary = Path(root) / spec["binary"]
    if not binary.is_file():
        return None
    try:
        result = subprocess.run([str(binary), *spec["version_args"]], cwd=root, capture_output=True,
                                text=True, timeout=10, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    import re
    match = re.search(spec["version_pattern"], (result.stdout or "") + "\n" + (result.stderr or ""))
    if not match:
        return None
    from cpa_mac.config import version_key
    value = match.group(1).strip()
    return value if version_key(value) else None


def write_default_config(root, spec):
    path = Path(root) / spec["config"]
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if spec["key"] == "cli":
        with path.open("x", encoding="utf-8") as output:
            yaml.safe_dump({"host": "127.0.0.1", "port": 8317, "auth-dir": "./auth",
                            "api-keys": [secrets.token_urlsafe(32)]}, output, allow_unicode=True, sort_keys=False)
        return
    with path.open("x", encoding="utf-8") as output:
        json.dump({"httpAddr": "127.0.0.1:18317", "dataDir": "./data"}, output, indent=2)
        output.write("\n")


def read_endpoint(root, spec):
    root = Path(root)
    if spec["key"] == "cli":
        try:
            data = yaml.safe_load((root / spec["config"]).read_text(encoding="utf-8-sig")) or {}
        except (OSError, yaml.YAMLError) as error:
            raise RuntimeError(f"无法读取服务配置（{type(error).__name__}）。") from None
        listener = data.get("server") if isinstance(data, dict) and isinstance(data.get("server"), dict) else data
        if not isinstance(listener, dict):
            raise RuntimeError("服务配置必须是 YAML 对象。")
        host, port = listener.get("host", "127.0.0.1"), listener.get("port", 8317)
    else:
        try:
            data = json.loads((root / spec["config"]).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"无法读取服务配置（{type(error).__name__}）。") from None
        if not isinstance(data, dict) or not isinstance(data.get("httpAddr"), str) or ":" not in data["httpAddr"]:
            raise RuntimeError("config.json 需要 httpAddr，例如 127.0.0.1:18317。")
        host, port_text = data["httpAddr"].rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            raise RuntimeError("config.json 的端口不正确。") from None
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("监听地址或端口不正确。")
    connect_host = "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host
    return connect_host, port


def _payload_root(stage, binary_name):
    matches = [path for path in stage.rglob(binary_name)
               if path.is_file() and path.name == binary_name and "__MACOSX" not in path.parts and not path.is_symlink()]
    if len(matches) != 1:
        raise RuntimeError(f"安装包中找不到唯一的 {binary_name}。")
    return matches[0].parent


def _files(stage):
    files = []
    for path in stage.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.name == ".DS_Store" or "__MACOSX" in path.parts:
            continue
        relative = path.relative_to(stage)
        if relative.parts[0] in PROTECTED or relative.as_posix() in PROTECTED:
            continue
        files.append(relative)
    if not files:
        raise RuntimeError("安装包为空。")
    return files


def apply_payload(stage, root, spec, report):
    root = Path(root)
    content = _payload_root(stage, spec["binary"])
    files = _files(content)
    root.mkdir(parents=True, exist_ok=True)
    backup = root / ".update-backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    for relative in files:
        source = root / relative
        if source.is_symlink() or (source.exists() and not source.resolve().is_relative_to(root.resolve())):
            raise RuntimeError("目标文件指向安装目录之外：" + relative.as_posix())
        if source.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    changed = []
    try:
        for index, relative in enumerate(files):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(content / relative, target)
            changed.append(relative)
            report(86 + 10 * (index + 1) / len(files), "正在安装：" + relative.as_posix())
        binary = root / spec["binary"]
        if not binary.is_file():
            raise RuntimeError(f"安装后找不到 {spec['binary']}。")
        os.chmod(binary, 0o755)
        if not (root / spec["config"]).exists():
            write_default_config(root, spec)
            report(97, "首次安装配置已创建，仅监听本机。")
    except Exception as failure:
        for relative in reversed(changed):
            backed = backup / relative
            target = root / relative
            if backed.is_file():
                shutil.copy2(backed, target)
            else:
                target.unlink(missing_ok=True)
        raise RuntimeError(f"{failure}；已尝试恢复旧文件。备份：{backup}") from failure
    return backup


def _wait_ready(process, host, port, log_path, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"程序启动失败，退出码 {process.returncode}。日志：{log_path}")
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"启动失败：{timeout} 秒内未监听 {host}:{port}。日志：{log_path}")


def start_server(root, spec, report):
    root = Path(root)
    binary = root / spec["binary"]
    if not binary.is_file():
        raise RuntimeError(f"找不到 {spec['binary']}，请先安装。")
    write_default_config(root, spec)
    host, port = read_endpoint(root, spec)
    log_path = root / "manager-service.log"
    handle = log_path.open("ab")
    try:
        process = subprocess.Popen([str(binary), *spec["start_args"]], cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=handle, stderr=handle, start_new_session=True)
    finally:
        handle.close()
    try:
        _wait_ready(process, host, port, log_path)
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    report(100, f"程序已启动（PID {process.pid}，端口 {port}）。")


def control(action, root, spec, report):
    if action not in ("start", "stop", "restart"):
        raise ValueError("未知的程序操作。")
    root = Path(root)
    with file_lock(root / "update.lock"):
        processes = running_processes(root, spec["binary"])
        if action == "start" and processes:
            report(100, "程序已在运行，无需重复启动。")
            return
        if action in ("stop", "restart"):
            if processes:
                report(None, "正在停止当前目录的程序…")
                stop_processes(processes)
            if action == "stop":
                report(100, "程序已停止。" if processes else "程序当前未运行。")
                return
        report(None, "正在启动程序…")
        start_server(root, spec, report)


def check(spec, root, proxy, arch, report, include_prerelease=False, force=False):
    local = local_version(root, spec)
    report(None, "本地版本：" + (local or "未安装或无法识别"))
    report(None, "正在检查 GitHub 最新版本…")
    release = github.fetch_latest(spec["repo"], proxy, include_prerelease=include_prerelease, force=force)
    asset = github.choose_project_asset(release["assets"], spec, arch)
    report(5, "最新版：" + release["tag"])
    return local, release, asset


def install(spec, root, proxy, arch, report, source_root, cancel=None, include_prerelease=False, workers=4):
    root = assert_install_directory(root, source_root)
    local, release, asset = check(spec, root, proxy, arch, report, include_prerelease=include_prerelease, force=True)
    if already_current(local, release["tag"]):
        report(100, f"无需更新：本地 {local} 已是最新版或高于发布版 {release['tag']}。")
        return release["tag"], asset["name"]
    with file_lock(root / "update.lock"), tempfile.TemporaryDirectory(prefix="cpa-mac-") as temporary:
        work = Path(temporary)
        archive = work / "package.tar.gz"
        github.download_verified(release, asset, archive, proxy, report, cancel, workers=workers)
        report(75, "正在解压安装包…")
        stage = extract_archive(archive, work / "stage", asset["name"])
        processes = running_processes(root, spec["binary"])
        if processes:
            report(None, "正在停止当前目录的程序…")
            stop_processes(processes)
        try:
            backup = apply_payload(stage, root, spec, report)
        except Exception:
            if processes:
                report(None, "安装未完成，正在尝试重新启动原来的程序…")
                try:
                    start_server(root, spec, report)
                except Exception as restart_error:
                    report(None, str(restart_error))
            raise
        if processes:
            report(98, "正在重新启动程序…")
            start_server(root, spec, report)
        report(100, f"安装完成：{release['tag']}。备份：{backup}")
    return release["tag"], asset["name"]
