"""Keep one manager window per executable path on Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import time

import psutil


WINDOW_TITLE = "CPA 统一管理器"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class SingleInstance:
    def __init__(self, executable: Path):
        identity = str(executable.resolve()).casefold().encode("utf-8")
        name = "Local\\CPA-Unified-Manager-" + hashlib.sha256(identity).hexdigest()
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.is_first = ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def focus_existing_window(executable: Path, timeout: float = 5.0) -> bool:
    """Find this executable's Tk window and restore it after a second launch."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (enum_callback, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)

    target = str(executable.resolve()).casefold()
    deadline = time.monotonic() + timeout
    while True:
        pids = set()
        for process in psutil.process_iter(["pid", "exe"]):
            try:
                path = process.info["exe"]
                if path and str(Path(path).resolve()).casefold() == target:
                    pids.add(process.info["pid"])
            except (psutil.Error, OSError):
                continue
        found = []

        @enum_callback
        def inspect(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title, length + 1)
                    if title.value == WINDOW_TITLE:
                        found.append(hwnd)
                        return False
            return True

        user32.EnumWindows(inspect, 0)
        if found:
            user32.ShowWindow(found[0], SW_RESTORE)
            user32.SetForegroundWindow(found[0])
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
