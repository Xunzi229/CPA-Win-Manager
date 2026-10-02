"""User-bound Windows DPAPI storage with atomic legacy JSON migration."""
import ctypes
from ctypes import wintypes
import json
import os
import uuid


MAGIC = b"CPA-SETTINGS\x00\x01"
SETTINGS_FILENAME = "manager-settings.dat"
LEGACY_SETTINGS_FILENAME = "manager-settings.json"


def _protect(data, *, decrypt=False):
    if os.name != "nt":
        raise OSError("配置加密仅支持 Windows。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; default scope is the current Windows user.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        action = "解密" if decrypt else "加密"
        raise OSError(ctypes.get_last_error(), f"配置{action}失败，请使用原 Windows 用户和机器。原文件未修改。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def write_settings(path, payload):
    encrypted = MAGIC + _protect(payload.encode("utf-8"))
    temporary = path.with_name(".manager-settings-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(encrypted)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_settings(path, *, migrate=True, legacy_path=None):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if legacy_path is not None and legacy_path.exists():
            value = read_settings(legacy_path, migrate=migrate)
            if migrate:
                os.replace(legacy_path, path)
            return value
        return {}
    encrypted = data.startswith(MAGIC)
    try:
        payload = _protect(data[len(MAGIC):], decrypt=True) if encrypted else data
        value = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("配置根节点必须为对象")
    except (ValueError, UnicodeError) as error:
        raise OSError("配置文件损坏，原文件未修改。") from error
    if not encrypted and migrate:
        write_settings(path, json.dumps(value, ensure_ascii=False))
    return value
