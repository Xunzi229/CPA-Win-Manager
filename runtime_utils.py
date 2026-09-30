"""Native process discovery and executable-version caching."""
import os
from pathlib import Path
import subprocess
import threading
import psutil


def monitor_work_area(x, y, fallback):
    """Work area of the monitor containing the anchor, including negative origins."""
    import ctypes
    from ctypes import wintypes
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    handle = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if handle and user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return info.work.left, info.work.top, info.work.right, info.work.bottom
    return fallback


def anchored_popup_position(anchor, size, work_area, gap=8):
    ax, ay, aw, ah = anchor
    width, height = size
    left, top, right, bottom = work_area
    x = ax + aw - width
    y = ay - height - gap
    if y < top + gap:
        y = ay + ah + gap
    x = max(left + gap, min(x, right - width - gap))
    y = max(top + gap, min(y, bottom - height - gap))
    return x, y


def discover_servers(root, executable, include_environment=False):
    expected = os.path.normcase(os.path.abspath(root / executable))
    servers = []
    for process in psutil.process_iter(["pid", "name"]):
        if (process.info.get("name") or "").lower() != executable.lower():
            continue
        try:
            if os.path.normcase(os.path.abspath(process.exe())) != expected:
                continue
            arguments = process.cmdline()
            server = {"ProcessId": process.pid,
                      "CommandLine": subprocess.list2cmdline(arguments) if arguments else None}
            if include_environment:
                server["Environment"] = process.environ()
            servers.append(server)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise RuntimeError("无法读取服务进程，请使用启动该服务的同一 Windows 用户运行管理器。") from None
    return servers


class VersionCache:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def read(self, target, executable, reader):
        path = Path(target) / executable
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        signature = (str(path.resolve()).lower(), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        with self.lock:
            if signature not in self.values:
                value = reader(target)
                # A transient inspection failure should be retried on the next refresh.
                if value is not None:
                    self.values = {signature: value}
                return value
            return self.values[signature]
