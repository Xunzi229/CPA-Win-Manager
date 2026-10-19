"""Plain JSON manager settings with migration from legacy DPAPI-encrypted files."""
import ctypes
from ctypes import wintypes
import json
import os
import time
import uuid


MAGIC = b"CPA-SETTINGS\x00\x01"
SETTINGS_FILENAME = "manager-settings.json"
LEGACY_SETTINGS_FILENAME = "manager-settings.dat"


def _unprotect(data):
    if os.name != "nt":
        raise OSError("旧版加密配置仅能在 Windows 上解密。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; DPAPI keys are lost after reinstalling Windows.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "旧版加密配置解密失败。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def _parse(data):
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise OSError("配置文件损坏，原文件未修改。") from error
    if not isinstance(value, dict):
        raise OSError("配置文件损坏，原文件未修改。")
    return value


def write_settings(path, payload):
    temporary = path.with_name(".manager-settings-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_settings(path, *, migrate=True, legacy_path=None, on_reset=None):
    source = path
    if not path.exists() and legacy_path is not None and legacy_path.exists():
        source = legacy_path
    try:
        data = source.read_bytes()
    except FileNotFoundError:
        return {}
    if data.startswith(MAGIC):
        try:
            value = _parse(_unprotect(data[len(MAGIC):]))
        except OSError:
            if migrate:
                backup = source.with_name(source.name + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
                os.replace(source, backup)
                if on_reset is not None:
                    on_reset(backup)
            return {}
    else:
        value = _parse(data)
    if migrate and (source != path or data.startswith(MAGIC)):
        write_settings(path, json.dumps(value, ensure_ascii=False, indent=2))
        if source != path:
            source.unlink()
    return value
