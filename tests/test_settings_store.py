"""Plain JSON storage, legacy DPAPI migration and failure preservation of manager settings."""
import ctypes
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpa_manager.core.settings_store import MAGIC, read_settings, write_settings


def protect(data):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    function = ctypes.WinDLL("crypt32").CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    assert function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    try:
        return MAGIC + ctypes.string_at(result.data, result.size)
    finally:
        ctypes.WinDLL("kernel32").LocalFree(result.data)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "manager-settings.json"
        self.legacy = Path(self.directory.name) / "manager-settings.dat"
        self.value = {"proxy_settings": {"url": "http://user:password@localhost:1080"},
                      "directory": "软件目录", "window": {"width": 1000}}
        self.payload = json.dumps(self.value, ensure_ascii=False)

    def test_plain_round_trip(self):
        write_settings(self.path, self.payload)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.value)
        self.assertEqual(read_settings(self.path), self.value)

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_legacy_encrypted_file_is_migrated(self):
        self.legacy.write_bytes(protect(self.payload.encode("utf-8")))
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy), self.value)
        self.assertFalse(self.legacy.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.value)

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_encrypted_content_in_new_filename_is_rewritten_as_plain(self):
        self.path.write_bytes(protect(self.payload.encode("utf-8")))
        self.assertEqual(read_settings(self.path), self.value)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.value)

    def test_undecryptable_legacy_is_backed_up_and_reset(self):
        broken = MAGIC + b"key from a previous Windows installation"
        self.legacy.write_bytes(broken)
        backups = []
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy, on_reset=backups.append), {})
        self.assertFalse(self.legacy.exists())
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].name.startswith("manager-settings.dat.bak-"))
        self.assertEqual(backups[0].read_bytes(), broken)

    def test_smoke_read_does_not_touch_undecryptable_legacy(self):
        broken = MAGIC + b"invalid protected data"
        self.legacy.write_bytes(broken)
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy, migrate=False), {})
        self.assertEqual(self.legacy.read_bytes(), broken)
        self.assertFalse(self.path.exists())

    def test_plain_legacy_is_migrated_and_smoke_keeps_it(self):
        self.legacy.write_text(self.payload, encoding="utf-8")
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy, migrate=False), self.value)
        self.assertTrue(self.legacy.exists())
        self.assertFalse(self.path.exists())
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy), self.value)
        self.assertFalse(self.legacy.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.value)

    def test_new_filename_has_priority(self):
        self.legacy.write_text(self.payload, encoding="utf-8")
        write_settings(self.path, "{}")
        self.assertEqual(read_settings(self.path, legacy_path=self.legacy), {})
        self.assertTrue(self.legacy.exists())

    def test_failed_migration_preserves_legacy(self):
        self.legacy.write_text(self.payload, encoding="utf-8")
        original = self.legacy.read_bytes()
        with patch("cpa_manager.core.settings_store.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                read_settings(self.path, legacy_path=self.legacy)
        self.assertEqual(self.legacy.read_bytes(), original)
        self.assertFalse(self.path.exists())

    def test_failed_write_preserves_original_and_removes_temporary_file(self):
        write_settings(self.path, self.payload)
        original = self.path.read_bytes()
        with patch("cpa_manager.core.settings_store.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                write_settings(self.path, "{}")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_settings_return_defaults_and_malformed_file_is_preserved(self):
        self.assertEqual(read_settings(self.path), {})
        self.path.write_bytes(b"{broken")
        with self.assertRaises(OSError):
            read_settings(self.path)
        self.assertEqual(self.path.read_bytes(), b"{broken")
