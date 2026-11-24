"""Restart the Windows manager with administrator privileges."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

import psutil

from cpa_manager.core.paths import ROOT


def is_elevated():
    """Return whether the current Windows process has an elevated token."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    check = shell32.IsUserAnAdmin
    check.argtypes = []
    check.restype = wintypes.BOOL
    return bool(check())


def launch_elevated_manager():
    """Request UAC approval and start a replacement that waits for this process."""
    frozen = getattr(sys, "frozen", False)
    executable = str(Path(sys.executable).resolve())
    arguments = ["--wait-for-pid", str(os.getpid())]
    if not frozen:
        arguments.insert(0, str(ROOT / "manager.py"))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell32.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = ctypes.c_ssize_t
    result = execute(None, "runas", executable, subprocess.list2cmdline(arguments), str(ROOT), 1)
    if result <= 32:
        raise OSError(result, "未能以管理员身份启动管理器。")


def wait_for_previous_manager(pid, timeout=120):
    """Allow the old GUI to release its single-instance mutex before startup."""
    try:
        previous = psutil.Process(pid)
        previous.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired as error:
        raise RuntimeError("旧管理器尚未退出，请关闭后重试。") from error
