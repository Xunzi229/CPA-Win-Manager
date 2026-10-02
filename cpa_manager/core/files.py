"""Windows filename validation for safe package extraction."""
import os
from pathlib import Path, PureWindowsPath


def reserved_name(name):
    if hasattr(os.path, "isreserved"):
        return os.path.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def safe_relative(value):
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(p in ("", ".", "..") or any(c in p for c in ':*?<>|"')
                   or any(ord(c) < 32 for c in p) or p.endswith((".", " "))
                   or reserved_name(p) for p in parts)):
        raise ValueError("无效的相对路径：" + value)
    return Path(*parts)
