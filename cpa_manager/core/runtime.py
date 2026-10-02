"""Native process discovery and executable-version caching."""
import os
from pathlib import Path
import subprocess
import threading
import psutil


def default_download_directory():
    """Resolve the user's Windows Downloads known folder, including redirection."""
    if os.name == "nt":
        import ctypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                        ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        query = shell32.SHGetKnownFolderPath
        query.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_wchar_p)]
        query.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        folder = GUID.from_buffer_copy(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
        value = ctypes.c_wchar_p()
        try:
            if query(ctypes.byref(folder), 0, None, ctypes.byref(value)) == 0 and value.value:
                return Path(value.value)
        finally:
            if value:
                ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
    return Path.home() / "Downloads"


def windows_architecture():
    """Return the native Windows architecture, including under x64 emulation."""
    import ctypes

    if os.name != "nt":
        raise RuntimeError("仅支持 Windows 系统。")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        is_wow64_process2 = kernel32.IsWow64Process2
    except AttributeError:
        # Older Windows versions do not expose IsWow64Process2.
        machine = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
    else:
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        is_wow64_process2.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort),
                                      ctypes.POINTER(ctypes.c_ushort)]
        is_wow64_process2.restype = ctypes.c_int
        process_machine, native_machine = ctypes.c_ushort(), ctypes.c_ushort()
        if not is_wow64_process2(kernel32.GetCurrentProcess(), ctypes.byref(process_machine),
                                 ctypes.byref(native_machine)):
            raise OSError(ctypes.get_last_error(), "无法读取 Windows 系统架构")
        machine = {0x8664: "AMD64", 0xAA64: "ARM64", 0x014c: "X86"}.get(native_machine.value, "")
    architecture = {"AMD64": "amd64", "ARM64": "arm64", "X86": "x86", "I386": "x86"}.get(machine.upper())
    if architecture is None:
        raise RuntimeError(f"不支持的 Windows 系统架构：{machine or '未知'}。")
    return architecture


def executable_architecture(path):
    """Read the architecture from a PE executable, if its header is valid."""
    try:
        with open(path, "rb") as executable:
            header = executable.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                return None
            offset = int.from_bytes(header[60:64], "little")
            if offset > 1024 * 1024:
                return None
            executable.seek(offset)
            header = executable.read(6)
            if header[:4] != b"PE\0\0":
                return None
            return {0x8664: "amd64", 0xAA64: "arm64"}.get(int.from_bytes(header[4:6], "little"))
    except OSError:
        return None


def architecture_compatible(path):
    installed = executable_architecture(path)
    return installed is None or installed == windows_architecture()


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
