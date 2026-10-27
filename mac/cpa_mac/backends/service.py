"""Install, update and control CLIProxyAPI and CPA-Manager-Plus on macOS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
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
from cpa_mac.core.macos import (
    check_port_listener,
    kill_port_listener,
    remove_quarantine,
    stop_directory_processes,
    install_launch_agent,
    uninstall_launch_agent,
    is_launch_agent_installed,
    is_launch_agent_loaded,
    notify,
    open_in_terminal,
    reveal_in_finder,
)


PROTECTED = {"config.yaml", "config.json", "manager-service.log", "manager-admin-key.txt", "update.lock"}


# ==============================================================================
# Homebrew 支持与 CLIProxyAPI 官方管理模式
# ==============================================================================

def find_brew_executable() -> str | None:
    direct = shutil.which("brew")
    if direct and os.path.isfile(direct) and os.access(direct, os.X_OK):
        return direct
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def is_brew_installed() -> bool:
    return find_brew_executable() is not None


def get_brew_env() -> dict[str, str]:
    env = os.environ.copy()
    extra_paths = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
    ]
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        try:
            for node_ver in sorted(nvm_dir.glob("v*"), reverse=True):
                bin_dir = node_ver / "bin"
                if bin_dir.is_dir():
                    extra_paths.append(str(bin_dir))
        except Exception:
            pass
    asdf_shims = Path.home() / ".asdf" / "shims"
    if asdf_shims.is_dir():
        extra_paths.append(str(asdf_shims))
    extra_paths.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])

    current_paths = env.get("PATH", "").split(os.pathsep)
    combined = []
    for p in extra_paths + current_paths:
        if p and p not in combined and os.path.isdir(p):
            combined.append(p)
    env["PATH"] = os.pathsep.join(combined)
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    return env


def get_brew_prefix() -> str:
    brew = find_brew_executable()
    if brew:
        try:
            res = subprocess.run([brew, "--prefix"], capture_output=True, text=True, env=get_brew_env(), timeout=5)
            prefix = res.stdout.strip()
            if res.returncode == 0 and prefix:
                return prefix
        except Exception:
            pass
    return "/opt/homebrew" if os.uname().machine == "arm64" else "/usr/local"


def cliproxyapi_paths() -> tuple[Path, Path]:
    prefix = get_brew_prefix()
    brew_conf = Path(prefix) / "etc" / "cliproxyapi.conf"
    home_conf = Path.home() / ".cli-proxy-api" / "config.yaml"
    return brew_conf, home_conf


def is_cliproxyapi_installed() -> bool:
    brew = find_brew_executable()
    if not brew:
        return False
    prefix = get_brew_prefix()
    bin_path = Path(prefix) / "bin" / "cliproxyapi"
    if bin_path.is_file():
        return True
    opt_bin = Path(prefix) / "opt" / "cliproxyapi" / "bin" / "cliproxyapi"
    if opt_bin.is_file():
        return True
    try:
        res = subprocess.run([brew, "list", "cliproxyapi"], capture_output=True, env=get_brew_env(), timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def cliproxyapi_local_version() -> str:
    brew = find_brew_executable()
    if not brew:
        return ""
    prefix = get_brew_prefix()
    bin_path = Path(prefix) / "bin" / "cliproxyapi"
    if not bin_path.is_file():
        bin_path = Path(prefix) / "opt" / "cliproxyapi" / "bin" / "cliproxyapi"
    if bin_path.is_file():
        try:
            res = subprocess.run([str(bin_path), "-v"], capture_output=True, text=True, env=get_brew_env(), timeout=5)
            out = (res.stdout or "") + "\n" + (res.stderr or "")
            import re
            m = re.search(r"CLIProxyAPI Version:\s*([^,\s]+)", out)
            if m:
                return m.group(1).strip()
            m = re.search(r"v?(\d+\.\d+\.\d+)", out)
            if m:
                return m.group(0).strip()
        except Exception:
            pass
    try:
        res = subprocess.run([brew, "list", "--versions", "cliproxyapi"], capture_output=True, text=True, env=get_brew_env(), timeout=5)
        parts = res.stdout.strip().split()
        if len(parts) >= 2:
            return parts[1]
    except Exception:
        pass
    return ""


def get_expected_cliproxyapi_executables() -> set[str]:
    """Return all valid executable paths (both symlinks and real canonical paths) for Homebrew cliproxyapi."""
    candidates: list[Path] = []
    which_bin = shutil.which("cliproxyapi")
    if which_bin:
        candidates.append(Path(which_bin))
    prefix = get_brew_prefix()
    if prefix:
        p_prefix = Path(prefix)
        candidates.append(p_prefix / "bin" / "cliproxyapi")
        candidates.append(p_prefix / "opt" / "cliproxyapi" / "bin" / "cliproxyapi")
        cellar_dir = p_prefix / "Cellar" / "cliproxyapi"
        if cellar_dir.is_dir():
            try:
                for ver_dir in cellar_dir.iterdir():
                    bin_dir = ver_dir / "bin"
                    if bin_dir.is_dir():
                        for entry in bin_dir.iterdir():
                            if entry.name.startswith("cliproxyapi"):
                                candidates.append(entry)
            except OSError:
                pass

    for p in list(candidates):
        try:
            if p.is_file() and p.stat().st_size < 10240:
                text = p.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("exec "):
                        import shlex
                        tokens = shlex.split(line[5:])
                        if tokens and tokens[0]:
                            candidates.append(Path(tokens[0]))
        except Exception:
            pass

    valid: set[str] = set()
    for p in candidates:
        valid.add(str(p))
        try:
            if p.exists():
                valid.add(str(p.resolve()))
        except (OSError, ValueError):
            pass
    return valid


def get_cliproxyapi_port() -> int:
    """Read the configured listening port for CLIProxyAPI (defaults to 8317)."""
    try:
        brew_conf, home_conf = cliproxyapi_paths()
        target = home_conf if home_conf.exists() else brew_conf
        if target.is_file():
            _, port = read_endpoint(target.parent, {"key": "cli", "config": target.name})
            return port
    except Exception:
        pass
    return 8317


def get_process_location_info(pid: int) -> dict:
    """Extract PID, executable path, process name, and command line details of a process."""
    exe_path = ""
    cmdline_str = ""
    proc_name = ""
    try:
        proc = psutil.Process(pid)
        proc_name = proc.name() or ""
        try:
            exe_path = proc.exe() or ""
        except (psutil.Error, OSError):
            pass
        try:
            cmdline_str = " ".join(proc.cmdline() or [])
        except (psutil.Error, OSError):
            pass
    except (psutil.Error, OSError):
        pass

    if not exe_path or not cmdline_str:
        try:
            res = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                full_cmd = res.stdout.strip()
                if not cmdline_str:
                    cmdline_str = full_cmd
                if not exe_path:
                    exe_path = full_cmd.split()[0]
        except Exception:
            pass

    display = exe_path or cmdline_str or proc_name or f"PID {pid}"
    return {
        "pid": pid,
        "name": proc_name,
        "exe": exe_path,
        "cmdline": cmdline_str,
        "display": display,
    }


def is_matching_cliproxyapi_process(pid: int, expected_paths: set[str] | None = None) -> bool:
    """Check if the process with the given PID is running the Homebrew cliproxyapi binary."""
    if expected_paths is None:
        expected_paths = get_expected_cliproxyapi_executables()

    prefix = get_brew_prefix()
    cellar_root = (Path(prefix) / "Cellar" / "cliproxyapi").resolve() if prefix else None
    opt_root = (Path(prefix) / "opt" / "cliproxyapi").resolve() if prefix else None

    def _matches_path(path_str: str) -> bool:
        if not path_str:
            return False
        if expected_paths and path_str in expected_paths:
            return True
        try:
            p = Path(path_str)
            resolved = p.resolve()
            if expected_paths and str(resolved) in expected_paths:
                return True
            if cellar_root and (resolved == cellar_root or cellar_root in resolved.parents):
                if resolved.name.startswith("cliproxyapi"):
                    return True
            if opt_root and (resolved == opt_root or opt_root in resolved.parents):
                if resolved.name.startswith("cliproxyapi"):
                    return True
            for ep in (expected_paths or ()):
                try:
                    if p.exists() and os.path.exists(ep) and os.path.samefile(p, ep):
                        return True
                except (OSError, ValueError):
                    pass
        except (OSError, ValueError):
            pass
        return False

    proc = None
    try:
        proc = psutil.Process(pid)
    except (psutil.Error, OSError):
        pass

    if proc:
        try:
            exe = proc.exe()
            if exe:
                return _matches_path(exe)
        except psutil.NoSuchProcess:
            return False
        except (psutil.Error, OSError):
            pass

        try:
            cmdline = proc.cmdline()
            if cmdline and cmdline[0]:
                return _matches_path(cmdline[0])
        except psutil.NoSuchProcess:
            return False
        except (psutil.Error, OSError):
            pass

    try:
        res = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            raw_argv0 = res.stdout.strip().split()[0]
            if _matches_path(raw_argv0):
                return True
    except Exception:
        pass

    return False


_last_cli_port_conflict: dict | None = None


def cliproxyapi_port_conflict() -> dict | None:
    """Check if the configured CLIProxyAPI port is occupied by another process."""
    global _last_cli_port_conflict
    return _last_cli_port_conflict


def cliproxyapi_service_status() -> tuple[str, bool]:
    global _last_cli_port_conflict
    if not is_cliproxyapi_installed():
        _last_cli_port_conflict = None
        return "未安装", False

    port = get_cliproxyapi_port()
    listener = check_port_listener(port)
    expected_bins = get_expected_cliproxyapi_executables()

    if listener:
        pid = listener["pid"]
        if is_matching_cliproxyapi_process(pid, expected_bins):
            _last_cli_port_conflict = None
            return f"运行中（PID {pid}）", True
        else:
            loc = get_process_location_info(pid)
            path_str = loc["display"]
            _last_cli_port_conflict = {
                "port": port,
                "pid": pid,
                "command": listener.get("command", ""),
                "path": path_str,
                "cmdline": loc.get("cmdline", ""),
            }
            return f"未运行（端口 {port} 被其他程序占用：PID {pid}，{path_str}）", False

    _last_cli_port_conflict = None
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name.startswith(("cliproxy", "cli-proxy")):
                continue
            if is_matching_cliproxyapi_process(proc.pid, expected_bins):
                pids.append(proc.pid)
        except (psutil.Error, OSError):
            continue
    if pids:
        return "运行中（PID " + ", ".join(str(p) for p in pids) + "）", True
    return "未运行", False


def install_cliproxyapi_brew(report, cancel=None) -> None:
    brew = find_brew_executable()
    if not brew:
        raise ValueError("未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：https://brew.sh")
    env = get_brew_env()
    report(5, "正在通过 Homebrew 安装 cliproxyapi ...")
    cmd = [brew, "install", "cliproxyapi"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in iter(proc.stdout.readline, ""):
        if cancel and cancel.is_set():
            proc.terminate()
            raise RuntimeError("任务已取消。")
        text = line.strip()
        if text:
            report(None, text)
    proc.stdout.close()
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"Homebrew 安装 cliproxyapi 失败 (退出码 {code})")
    
    report(60, "配置配置文件路径及软链接 ...")
    brew_conf, home_conf = cliproxyapi_paths()
    home_dir = home_conf.parent
    home_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run([brew, "services", "stop", "cliproxyapi"], capture_output=True, env=env)

    if brew_conf.is_file() and not brew_conf.is_symlink():
        if not home_conf.exists():
            shutil.copy2(brew_conf, home_conf)
        import time
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_name = f"{brew_conf}.bak.{ts}"
        try:
            shutil.move(str(brew_conf), backup_name)
        except OSError:
            pass

    if not home_conf.exists():
        default_config = (
            "# CLIProxyAPI 配置文件\n"
            "server:\n"
            "  host: 127.0.0.1\n"
            "  port: 8317\n"
        )
        home_conf.write_text(default_config, encoding="utf-8")

    brew_conf.parent.mkdir(parents=True, exist_ok=True)
    if brew_conf.is_symlink() or brew_conf.exists():
        try:
            brew_conf.unlink()
        except OSError:
            pass
    try:
        brew_conf.symlink_to(home_conf)
    except OSError as error:
        report(None, f"创建配置软链提示：{error}")

    report(80, "正在启动 Homebrew 服务 ...")
    port = get_cliproxyapi_port()
    listener = check_port_listener(port)
    if listener and not is_matching_cliproxyapi_process(listener["pid"]):
        loc = get_process_location_info(listener["pid"])
        report(None, f"提示：端口 {port} 已被其他程序占用（PID {listener['pid']}，路径：{loc['display']}），跳过自动启动。请先停止占用端口的程序后再启动服务。")
    else:
        res = subprocess.run([brew, "services", "start", "cliproxyapi"], capture_output=True, text=True, env=env)
        if res.returncode != 0:
            err = (res.stderr or res.stdout).strip()
            report(None, f"启动服务提示：{err}")
    report(100, "CLIProxyAPI 已成功通过 Homebrew 安装并完成服务配置。")


def control_cliproxyapi_brew(action: str, report) -> None:
    brew = find_brew_executable()
    if not brew:
        raise ValueError("未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：https://brew.sh")
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"不支持的服务操作：{action}")
    env = get_brew_env()
    if action in ("start", "restart"):
        port = get_cliproxyapi_port()
        listener = check_port_listener(port)
        if listener:
            expected_bins = get_expected_cliproxyapi_executables()
            if not is_matching_cliproxyapi_process(listener["pid"], expected_bins):
                loc = get_process_location_info(listener["pid"])
                loc_str = loc["display"]
                act_cn = "启动" if action == "start" else "重启"
                raise RuntimeError(
                    f"端口 {port} 已被其他程序占用（PID {listener['pid']}，路径：{loc_str}），无法{act_cn} Homebrew 服务。请先停止占用该端口的程序。"
                )
            elif action == "start":
                report(100, f"CLIProxyAPI 服务已在运行中（PID {listener['pid']}）。")
                return

        brew_conf, home_conf = cliproxyapi_paths()
        home_dir = home_conf.parent
        home_dir.mkdir(parents=True, exist_ok=True)
        if not home_conf.exists() and brew_conf.is_file() and not brew_conf.is_symlink():
            shutil.copy2(brew_conf, home_conf)
        if not home_conf.exists():
            home_conf.write_text("server:\n  host: 127.0.0.1\n  port: 8317\n", encoding="utf-8")
        if brew_conf.parent.exists():
            if brew_conf.is_symlink() or not brew_conf.exists():
                if brew_conf.is_symlink():
                    try:
                        brew_conf.unlink()
                    except OSError:
                        pass
                try:
                    brew_conf.symlink_to(home_conf)
                except OSError:
                    pass
    action_cn = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    report(30, f"正在执行 brew services {action} cliproxyapi ...")
    res = subprocess.run([brew, "services", action, "cliproxyapi"], capture_output=True, text=True, env=env)
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        raise RuntimeError(f"执行 brew services {action} 失败：{err}")
    report(100, f"CLIProxyAPI 服务已{action_cn}。")


def _write_executable(path: Path, write) -> None:
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex + ".tmp")
    try:
        write(temporary)
        os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update_cliproxyapi_github(spec, proxy, arch, report, cancel=None, include_prerelease=False, workers=4) -> str:
    """Download the latest GitHub release and replace the Homebrew-managed cliproxyapi binary."""
    if not find_brew_executable():
        raise ValueError("未检测到 Homebrew (brew)。macOS 下 CLIProxyAPI 依赖 Homebrew 安装与管理，请先安装 Homebrew：https://brew.sh")
    prefix = Path(get_brew_prefix())
    opt_dir = prefix / "opt" / "cliproxyapi"
    if not opt_dir.exists():
        raise RuntimeError("尚未通过 Homebrew 安装 CLIProxyAPI，请先点击“安装”。")
    bin_dir = opt_dir.resolve() / "bin"
    local = cliproxyapi_local_version()
    report(None, "本地版本：" + (local or "未安装或无法识别"))
    report(None, "正在检查 GitHub 最新版本…")
    release = github.fetch_latest(spec["repo"], proxy, include_prerelease=include_prerelease, force=True)
    if already_current(local, release["tag"]):
        report(100, f"无需更新：本地 {local} 已是最新版或高于发布版 {release['tag']}。")
        return release["tag"]
    asset = github.choose_project_asset(release["assets"], spec, arch)
    version = release["tag"].removeprefix("v")
    with tempfile.TemporaryDirectory(prefix="cpa-mac-") as temporary:
        work = Path(temporary)
        archive = work / "package.tar.gz"
        github.download_verified(release, asset, archive, proxy, report, cancel, workers=workers)
        report(75, "正在解压安装包…")
        stage = extract_archive(archive, work / "stage", asset["name"])
        source = _payload_root(stage, spec["binary"]) / spec["binary"]
        target = bin_dir / f"cliproxyapi-{version}"
        report(85, "正在替换 Homebrew 中的 cliproxyapi：" + str(target))
        _write_executable(target, lambda path: shutil.copy2(source, path))
    result = subprocess.run([str(target), *spec["version_args"]], capture_output=True, text=True, timeout=15)
    match = re.search(spec["version_pattern"], (result.stdout or "") + "\n" + (result.stderr or ""))
    if not match or match.group(1).removeprefix("v") != version:
        target.unlink(missing_ok=True)
        raise RuntimeError("新版本程序校验失败，已保留原版本。")
    wrapper = bin_dir / "cliproxyapi"
    if wrapper.is_file() and not wrapper.read_bytes().startswith(b"#!"):
        os.replace(wrapper, bin_dir / "cliproxyapi-brew")
    config = prefix / "etc" / "cliproxyapi.conf"
    script = f'#!/bin/sh\nexec "{target}" -config "{config}" "$@"\n'
    _write_executable(wrapper, lambda path: path.write_text(script, encoding="utf-8"))
    _, running = cliproxyapi_service_status()
    if running:
        report(92, "正在重启 CLIProxyAPI 服务…")
        control_cliproxyapi_brew("restart", report)
    for old in bin_dir.glob("cliproxyapi-*"):
        if old != target and old.name != "cliproxyapi-brew":
            old.unlink(missing_ok=True)
    report(100, f"CLIProxyAPI 已更新到 {release['tag']}（GitHub 发布版）。")
    return release["tag"]


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
        try:
            _, port = read_endpoint(root, spec)
            listener = check_port_listener(port)
            if listener:
                loc = get_process_location_info(listener["pid"])
                return f"未运行（端口 {port} 被其他程序占用：PID {listener['pid']}，{loc['display']}）"
        except Exception:
            pass
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
    value = match.group(1 if match.lastindex else 0).strip()
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


def management_url(root, spec):
    if spec["key"] == "cli":
        status, is_running = cliproxyapi_service_status()
        if not is_running:
            raise RuntimeError("服务未运行，请先启动服务再打开后台。")
        brew_conf, home_conf = cliproxyapi_paths()
        cfg_path = home_conf if home_conf.exists() else brew_conf
        if cfg_path.is_file():
            host, port = read_endpoint(cfg_path.parent, spec)
        else:
            host, port = "127.0.0.1", 8317
    else:
        if not running_processes(root, spec["binary"]):
            raise RuntimeError("服务未运行，请先启动服务再打开后台。")
        host, port = read_endpoint(root, spec)
    import ipaddress
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host != "localhost":
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise RuntimeError("服务监听地址不是有效的 IP 或 localhost，请检查配置。") from None
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError:
        raise RuntimeError("服务尚未监听或已退出，请检查启动日志后重试。") from None
    authority = "[" + host + "]" if ":" in host else host
    return f"http://{authority}:{port}/management.html"


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
        remove_quarantine(binary)
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
    listener = check_port_listener(port)
    if listener:
        procs = running_processes(root, spec["binary"])
        if any(p.pid == listener["pid"] for p in procs):
            report(100, f"程序已在运行中（PID {listener['pid']}，端口 {port}）。")
            return
        loc = get_process_location_info(listener["pid"])
        raise RuntimeError(f"端口 {port} 已被其他程序占用（PID {listener['pid']}，路径：{loc['display']}），无法启动服务。请先停止占用该端口的程序。")
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
        stopped = stop_directory_processes(root)
        if stopped:
            report(None, "已结束安装目录中的进程：" + ", ".join(map(str, stopped)))
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


# ==============================================================================
# 管理员 Key 与运维辅助 (参考 Windows CPA-Manager-Plus 实现)
# ==============================================================================

def save_admin_key(root, key):
    key = (key or "").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("登录 Key 不能为空且不能包含空格。")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manager-admin-key.txt"
    temp = root / (".manager-key-" + secrets.token_hex(4) + ".tmp")
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(key + "\n")
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def find_admin_key(root):
    root = Path(root).expanduser().resolve()
    cache = root / "manager-admin-key.txt"
    cached, saved_at = "", 0
    if cache.is_file():
        try:
            cached = cache.read_text(encoding="utf-8").strip()
            saved_at = cache.stat().st_mtime
        except OSError:
            cached = ""
    import re
    paths = [root / "manager-service.log", root / "logs" / "cpa-manager-plus.log",
             root / "logs" / "cpa-manager-plus.err.log"]
    paths = sorted((p for p in paths if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        if path.stat().st_mtime <= saved_at and cached:
            continue
        try:
            with path.open("rb") as log:
                log.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
                text = log.read().decode("utf-8", errors="replace")
            matches = list(re.finditer(r"(?m)^(?:(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+)?[^\n]*?CPA Manager Plus admin key generated:\s*(\S+)", text))
            if matches:
                generated_at, key = matches[-1].groups()
                if cached and key != cached:
                    try:
                        timestamp = time.mktime(time.strptime(generated_at, "%Y/%m/%d %H:%M:%S")) if generated_at else 0
                    except ValueError:
                        timestamp = 0
                    if timestamp <= saved_at:
                        continue
                if key != cached:
                    save_admin_key(root, key)
                return key
        except OSError:
            continue
    return cached


def reset_admin_key(root):
    root = Path(root).expanduser().resolve()
    binary = root / "cpa-manager-plus"
    if not binary.is_file():
        raise RuntimeError("找不到 cpa-manager-plus 可执行文件，请先安装或检查解压目录。")
    try:
        os.chmod(binary, 0o755)
    except OSError:
        pass
    remove_quarantine(binary)
    db_path = root / "data" / "usage.sqlite"
    cmd = [str(binary), "reset-admin-key", "--db-path", str(db_path)]
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"重置管理员 Key 失败：{combined or f'命令退出码 {result.returncode}'}")
    import re
    match = re.search(r"New admin key:\s*(\S+)", combined)
    if not match:
        raise RuntimeError(f"未能从输出中解析出新 Key：{combined}")
    new_key = match.group(1).strip()
    save_admin_key(root, new_key)
    return new_key


def admin_key_status(root):
    try:
        key = find_admin_key(root)
    except (OSError, ValueError) as error:
        return {"admin_key": "", "admin_key_note": f"读取 Key 失败（{type(error).__name__}）；请检查目录权限或录入已有 Key。服务控制不受影响。"}
    if key:
        return {"admin_key": key, "admin_key_note": "已读取本地 Key，使用当前用户私有文件保存；录入仅保存副本，不修改服务登录凭据。"}
    return {"admin_key": "", "admin_key_note": "未找到已保存或日志中的明文 Key。服务数据库只保存摘要，不能反推原文；请录入已有 Key。首次初始化的日志有记录时可自动捕获，重启已有服务不会重新生成。"}


def backup_directories(root):
    root = Path(root).expanduser().resolve()
    parent = root / ".update-backups"
    if not parent.is_dir() or parent.is_symlink():
        return []
    import re
    return [p for p in parent.iterdir() if p.is_dir() and not p.is_symlink()
            and re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", p.name)]


def backup_count(root):
    return len(backup_directories(root))


def clear_backups(root, report=None):
    root = Path(root).expanduser().resolve()
    backups = backup_directories(root)
    if not backups:
        if report:
            report(100, "没有可清理的安装备份。")
        return 0
    deleted, errors = 0, []
    for index, backup in enumerate(backups):
        try:
            shutil.rmtree(backup)
            deleted += 1
        except OSError as error:
            errors.append(backup.name + "：" + str(error))
        if report:
            report(100 * (index + 1) / len(backups), "正在清理安装备份…")
    if errors:
        raise RuntimeError(f"已清理 {deleted} 个备份，部分失败：\n" + "\n".join(errors))
    if report:
        report(100, f"已清理 {deleted} 个安装备份。")
    return deleted


def open_config(root, spec):
    root = Path(root).expanduser().resolve()
    path = root / spec["config"]
    if not path.is_file():
        write_default_config(root, spec)
    subprocess.run(["open", str(path)], check=False)
    return str(path)


def open_log(root):
    root = Path(root).expanduser().resolve()
    path = root / "manager-service.log"
    if not path.is_file():
        path.touch()
    subprocess.run(["open", str(path)], check=False)
    return str(path)


# ==============================================================================
# macOS 原生 LaunchAgent 守护与系统集成
# ==============================================================================

def service_launch_agent_label(spec):
    return "com.cpa." + ("cliproxyapi" if spec["key"] == "cli" else "cpamanagerplus")


def launch_agent_status(spec):
    label = service_launch_agent_label(spec)
    installed = is_launch_agent_installed(label)
    return {
        "installed": installed,
        "loaded": is_launch_agent_loaded(label) if installed else False,
        "label": label,
    }


def enable_launch_agent(root, spec):
    root = Path(root).expanduser().resolve()
    binary = root / spec["binary"]
    if not binary.is_file():
        raise RuntimeError(f"找不到 {spec['binary']}，请先安装后再配置守护进程。")
    write_default_config(root, spec)
    remove_quarantine(binary)
    os.chmod(binary, 0o755)
    label = service_launch_agent_label(spec)
    log_dir = root / "logs"
    plist = install_launch_agent(
        label=label,
        executable=binary,
        args=spec["start_args"],
        work_dir=root,
        log_dir=log_dir,
        keep_alive=True,
        run_at_load=True,
    )
    notify("macOS 守护已启用", f"{spec['title']} 已配置为开机自启常驻。")
    return str(plist)


def disable_launch_agent(spec):
    label = service_launch_agent_label(spec)
    uninstall_launch_agent(label)
    notify("macOS 守护已停用", f"{spec['title']} 已取消开机自启常驻。")
    return True


def release_service_port(root, spec):
    _, port = read_endpoint(root, spec)
    killed = kill_port_listener(port)
    return {"port": port, "released": killed}


def tail_log_in_terminal(root):
    root = Path(root).expanduser().resolve()
    log_path = root / "manager-service.log"
    if not log_path.is_file():
        log_path.touch()
    return open_in_terminal(root, "tail -f -n 50 manager-service.log")
