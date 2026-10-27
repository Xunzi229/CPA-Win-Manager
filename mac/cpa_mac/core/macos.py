"""macOS system-level adaptations and native integrations.

Includes:
- AppleScript notification center triggers
- Port listener discovery via lsof and port cleanup
- Launchd LaunchAgent plist lifecycle (auto-start, keep-alive)
- Gatekeeper quarantine removal (xattr)
- Automated DMG mount, extract .app to /Applications, and unmount
- User PATH symlink creation (~/.local/bin)
- Reveal in Finder (open -R) and Terminal launcher
- macOS system health diagnostics (Architecture, ports, paths)
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import time

import psutil


def notify(title: str, message: str, subtitle: str = "") -> bool:
    """Send a native macOS Notification Center banner via osascript."""
    try:
        esc_title = title.replace('\\', '\\\\').replace('"', '\\"')
        esc_msg = message.replace('\\', '\\\\').replace('"', '\\"')
        esc_sub = subtitle.replace('\\', '\\\\').replace('"', '\\"')
        cmd = f'display notification "{esc_msg}" with title "{esc_title}"'
        if esc_sub:
            cmd += f' subtitle "{esc_sub}"'
        res = subprocess.run(["osascript", "-e", cmd], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def check_port_listener(port: int) -> dict | None:
    """Detect which process is listening on the specified port using lsof."""
    try:
        res = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None
        lines = [line.strip() for line in res.stdout.strip().splitlines()]
        if len(lines) < 2:
            return None
        parts = re.split(r"\s+", lines[1])
        if len(parts) >= 2:
            command_name = parts[0]
            pid = int(parts[1])
            return {"port": port, "pid": pid, "command": command_name, "raw": lines[1]}
    except Exception:
        pass
    return None


def kill_port_listener(port: int) -> bool:
    """Kill the process occupying the port."""
    info = check_port_listener(port)
    if not info:
        return True
    try:
        os.kill(info["pid"], 15)  # SIGTERM
        time.sleep(0.5)
        if check_port_listener(port):
            os.kill(info["pid"], 9)  # SIGKILL
            time.sleep(0.2)
        return check_port_listener(port) is None
    except OSError:
        return False


def remove_quarantine(path: Path | str) -> bool:
    """Remove macOS Gatekeeper quarantine attribute (com.apple.quarantine)."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return False
    import sys
    if sys.platform != "darwin":
        return True
    try:
        res = subprocess.run(["xattr", "-rd", "com.apple.quarantine", str(p)], capture_output=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False


def launch_agents_dir() -> Path:
    """Return user LaunchAgents directory (~/Library/LaunchAgents)."""
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_launch_agent_plist(label: str) -> Path:
    return launch_agents_dir() / f"{label}.plist"


def is_launch_agent_installed(label: str) -> bool:
    return get_launch_agent_plist(label).is_file()


def is_launch_agent_loaded(label: str) -> bool:
    """Check if the LaunchAgent is currently loaded into launchctl."""
    try:
        res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def install_launch_agent(
    label: str,
    executable: str | Path,
    args: list[str],
    work_dir: str | Path,
    log_dir: str | Path,
    keep_alive: bool = True,
    run_at_load: bool = True,
) -> Path:
    """Create and load a macOS LaunchAgent plist."""
    plist_path = get_launch_agent_plist(label)
    work = Path(work_dir).expanduser().resolve()
    logs = Path(log_dir).expanduser().resolve()
    logs.mkdir(parents=True, exist_ok=True)
    exe = Path(executable).expanduser().resolve()

    plist_data = {
        "Label": label,
        "ProgramArguments": [str(exe)] + list(args),
        "WorkingDirectory": str(work),
        "StandardOutPath": str(logs / f"{label}.log"),
        "StandardErrorPath": str(logs / f"{label}.err.log"),
        "RunAtLoad": bool(run_at_load),
        "KeepAlive": bool(keep_alive),
        "ProcessType": "Interactive",
    }

    if is_launch_agent_loaded(label):
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)

    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True)
    return plist_path


def uninstall_launch_agent(label: str) -> bool:
    """Unload and remove a macOS LaunchAgent plist."""
    plist_path = get_launch_agent_plist(label)
    if is_launch_agent_loaded(label):
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
    if plist_path.is_file():
        plist_path.unlink(missing_ok=True)
        return True
    return False


def reveal_in_finder(path: str | Path) -> bool:
    """Reveal file or directory highlighted in macOS Finder (open -R)."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return False
    res = subprocess.run(["open", "-R", str(p)], capture_output=True)
    return res.returncode == 0


def open_in_terminal(directory: str | Path, command: str = "") -> bool:
    """Open a new Terminal or iTerm2 window at the directory."""
    d = Path(directory).expanduser().resolve()
    if not d.is_dir():
        return False
    # If a specific command is supplied, execute it via AppleScript
    if command:
        script = f'tell application "Terminal" to do script "cd {d} && {command}" activate'
    else:
        script = f'tell application "Terminal" to do script "cd {d}" activate'
    res = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    return res.returncode == 0


def link_to_user_bin(binary_path: str | Path, link_name: str = "") -> Path:
    """Create a symlink for a CLI tool in ~/.local/bin or /usr/local/bin."""
    src = Path(binary_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"源二进制文件不存在: {src}")
    name = link_name or src.name
    user_bin = Path.home() / ".local" / "bin"
    user_bin.mkdir(parents=True, exist_ok=True)
    link = user_bin / name
    if link.is_symlink() or link.is_file():
        link.unlink(missing_ok=True)
    try:
        link.symlink_to(src)
    except OSError:
        shutil.copy2(src, link)
    return link


def stop_directory_processes(root, timeout=10):
    """Force-stop processes whose executable is inside one install directory."""
    raw_root = Path(os.path.abspath(root))
    try:
        resolved_root = Path(root).expanduser().resolve()
    except OSError:
        resolved_root = raw_root
    if resolved_root in (
        Path(raw_root.anchor).resolve(),
        Path.home().resolve(),
        Path("/Applications").resolve(),
        Path("/System/Applications").resolve(),
        (Path.home() / "Applications").resolve(),
    ):
        raise ValueError("不会结束整个应用程序目录中的进程。")
    if not resolved_root.exists():
        return []
    protected = {os.getpid()}
    try:
        protected.update(parent.pid for parent in psutil.Process().parents())
    except psutil.Error:
        pass
    stopped = []
    for _ in range(3):
        processes = []
        for process in psutil.process_iter(["pid", "exe"]):
            if process.pid in protected or process.pid in stopped:
                continue
            try:
                path = process.info.get("exe") or process.exe()
                if not path:
                    continue
                candidate = Path(os.path.abspath(path))
                if candidate.is_relative_to(raw_root) or Path(path).resolve().is_relative_to(resolved_root):
                    processes.append(process)
            except (psutil.Error, OSError, ValueError):
                continue
        if not processes:
            break
        for process in processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as error:
                raise RuntimeError(f"无法结束占用安装目录的进程 PID {process.pid}。") from error
        _, alive = psutil.wait_procs(processes, timeout=timeout)
        if alive:
            raise RuntimeError("以下进程仍在占用安装目录：" + ", ".join(str(process.pid) for process in alive))
        stopped.extend(process.pid for process in processes)
    return stopped


def install_dmg_app(dmg_path: str | Path, target_dir: str | Path = "/Applications", report=None) -> Path:
    """Mount a macOS .dmg disk image, extract any .app bundle into Applications, and unmount."""
    dmg = Path(dmg_path).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    if not dmg.is_file():
        raise FileNotFoundError(f"DMG 文件不存在: {dmg}")

    mount_point = Path(tempfile.mkdtemp(prefix="cpa-dmg-mount-"))
    try:
        if report:
            report(None, f"正在挂载磁盘镜像 {dmg.name}…")
        # hdiutil attach -nobrowse -readonly -mountpoint <mount_point> <dmg>
        attach_res = subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount_point), str(dmg)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if attach_res.returncode != 0:
            raise RuntimeError(f"挂载 DMG 失败: {attach_res.stderr or attach_res.stdout}")

        # 扫描挂载目录内的 .app 目录
        apps = [p for p in mount_point.glob("*.app") if p.is_dir()]
        if not apps:
            apps = [p for p in mount_point.glob("**/*.app") if p.is_dir()]
        if not apps:
            raise RuntimeError(f"未在 DMG 镜像中找到应用程序包 (.app)。")

        app_src = apps[0]
        app_dst = target / app_src.name

        if report:
            report(None, f"正在安装 {app_src.name} 到 {target}…")

        if app_dst.exists():
            stopped = stop_directory_processes(app_dst)
            if stopped and report:
                report(None, "已结束正在运行的程序：" + ", ".join(map(str, stopped)))
            shutil.rmtree(app_dst)

        # 使用 macOS 原生 ditto 完整保留代码签名、权限与扩展属性
        ditto_res = subprocess.run(["ditto", str(app_src), str(app_dst)], capture_output=True, text=True, timeout=120)
        if ditto_res.returncode != 0:
            # 回退使用 copytree
            shutil.copytree(app_src, app_dst, symlinks=True)

        # 移除 Gatekeeper 隔离属性
        remove_quarantine(app_dst)

        if report:
            report(100, f"应用程序已成功安装至 {app_dst}")

        return app_dst
    finally:
        # 无论如何安全卸载挂载点
        subprocess.run(["hdiutil", "detach", str(mount_point), "-force"], capture_output=True)
        shutil.rmtree(mount_point, ignore_errors=True)


def system_doctor() -> dict:
    """Run comprehensive macOS system health check for CPA Manager."""
    import platform
    arch = os.uname().machine if hasattr(os, "uname") else platform.machine()
    kernel = os.uname().release if hasattr(os, "uname") else platform.release()
    mac_ver = platform.mac_ver()[0] or "未知"

    local_bin = str(Path.home() / ".local" / "bin")
    path_env = os.environ.get("PATH", "")
    has_local_bin = local_bin in path_env.split(":")

    # 检查默认核心服务端口
    port_8317 = check_port_listener(8317)
    port_18317 = check_port_listener(18317)

    # 检查 LaunchAgents 状态
    cli_agent = is_launch_agent_installed("com.cpa.cliproxyapi")
    plus_agent = is_launch_agent_installed("com.cpa.cpamanagerplus")

    # 磁盘空间
    usage = shutil.disk_usage(Path.home())
    free_gb = round(usage.free / (1024 ** 3), 1)

    return {
        "os": "macOS",
        "mac_version": mac_ver,
        "kernel": kernel,
        "architecture": arch,
        "is_apple_silicon": arch == "arm64",
        "free_disk_gb": free_gb,
        "has_local_bin_in_path": has_local_bin,
        "local_bin": local_bin,
        "ports": {
            "8317": port_8317 or {"status": "空闲可用"},
            "18317": port_18317 or {"status": "空闲可用"},
        },
        "launch_agents": {
            "cli": {"installed": cli_agent, "loaded": is_launch_agent_loaded("com.cpa.cliproxyapi") if cli_agent else False},
            "plus": {"installed": plus_agent, "loaded": is_launch_agent_loaded("com.cpa.cpamanagerplus") if plus_agent else False},
        },
    }


def scan_installed_applications(custom_dirs: list[str | Path] | None = None) -> list[dict]:
    """Scan installed macOS applications from /Applications, /System/Applications, and ~/Applications."""
    dirs_to_scan = []
    if custom_dirs:
        dirs_to_scan.extend(Path(d).expanduser() for d in custom_dirs)
    else:
        dirs_to_scan.extend([
            Path("/Applications"),
            Path("/System/Applications"),
            Path.home() / "Applications",
        ])
    found = []
    seen_paths = set()
    for base in dirs_to_scan:
        if not base.is_dir():
            continue
        try:
            app_paths = list(base.glob("*.app"))
            for sub in base.iterdir():
                if sub.is_dir() and not sub.name.endswith(".app") and not sub.name.startswith("."):
                    try:
                        app_paths.extend(sub.glob("*.app"))
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            continue

        for app in app_paths:
            try:
                resolved = str(app.resolve())
            except (OSError, RuntimeError):
                resolved = str(app)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            info_plist = app / "Contents" / "Info.plist"
            name = app.stem
            version = "未知"
            bundle_id = ""
            if info_plist.is_file():
                try:
                    with open(info_plist, "rb") as f:
                        plist = plistlib.load(f)
                    name = plist.get("CFBundleDisplayName") or plist.get("CFBundleName") or app.stem
                    version = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion") or "未知"
                    bundle_id = plist.get("CFBundleIdentifier") or ""
                except Exception:
                    pass
            found.append({
                "id": bundle_id or app.name,
                "name": str(name).strip(),
                "version": str(version).strip(),
                "directory": str(app),
            })
    found.sort(key=lambda item: item["name"].casefold())
    return found
