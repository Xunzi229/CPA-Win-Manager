"""Temporarily stop Windows services whose executables are being replaced."""
from contextlib import contextmanager
from pathlib import Path
import shlex
import subprocess
import sys
import time

import psutil


def _service_executable(command):
    try:
        return shlex.split(command, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        return ""


def _matching_services(root, relative_files):
    resolved_root = Path(root).resolve()
    targets = ({(resolved_root / name).resolve() for name in relative_files}
               if relative_files is not None else None)
    matches = []
    for service in psutil.win_service_iter():
        try:
            info = service.as_dict()
            executable = _service_executable(info.get("binpath") or "")
            path = Path(executable).resolve() if executable else None
            if info.get("status") == "running" and path and (
                    path.is_relative_to(resolved_root) if targets is None else path in targets):
                matches.append(info["name"])
        except (psutil.Error, OSError):
            continue
    return matches


def _change_service(name, action, timeout=30):
    result = subprocess.run(["sc.exe", action, name], capture_output=True, text=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    expected = "stopped" if action == "stop" else "running"
    if psutil.win_service_get(name).status() == expected:
        return
    if result.returncode:
        raise RuntimeError(f"无法{'停止' if action == 'stop' else '启动'}占用安装目录的服务 {name}，请以管理员身份运行管理器。")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if psutil.win_service_get(name).status() == expected:
            return
        time.sleep(0.2)
    raise RuntimeError(f"等待服务 {name} {'停止' if action == 'stop' else '启动'}超时。")


@contextmanager
def paused_target_services(root, relative_files, report):
    stopped = []
    try:
        for name in _matching_services(root, relative_files):
            _change_service(name, "stop")
            stopped.append(name)
            report(81, "已停止占用安装目录的服务：" + name)
        yield
    finally:
        active_error = sys.exc_info()[0] is not None
        failures = []
        for name in reversed(stopped):
            try:
                _change_service(name, "start")
                report(100, "已恢复服务：" + name)
            except Exception as error:
                failures.append(str(error))
        if failures and not active_error:
            raise RuntimeError("；".join(failures))
