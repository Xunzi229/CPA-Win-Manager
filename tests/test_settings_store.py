"""Encryption, migration and failure preservation of manager settings."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpa_manager.core.settings_store import MAGIC, read_settings, write_settings


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "manager-settings.json"
        self.value = {"proxy_settings": {"url": "http://secret:password@localhost:1080"},
                      "directory": "软件目录", "window": {"width": 1000}}
        self.payload = json.dumps(self.value, ensure_ascii=False)

    def test_encrypted_round_trip_and_randomized_ciphertext(self):
        write_settings(self.path, self.payload)
        first = self.path.read_bytes()
        self.assertTrue(first.startswith(MAGIC))
        self.assertNotIn(b"password", first)
        self.assertEqual(read_settings(self.path), self.value)
        write_settings(self.path, self.payload)
        self.assertNotEqual(self.path.read_bytes(), first)

    def test_legacy_configuration_is_migrated(self):
        self.path.write_text(self.payload, encoding="utf-8")
        self.assertEqual(read_settings(self.path), self.value)
        self.assertTrue(self.path.read_bytes().startswith(MAGIC))
        self.assertEqual(read_settings(self.path), self.value)

    def test_smoke_read_does_not_migrate(self):
        self.path.write_text(self.payload, encoding="utf-8")
        original = self.path.read_bytes()
        self.assertEqual(read_settings(self.path, migrate=False), self.value)
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_ciphertext_is_preserved(self):
        broken = MAGIC + b"invalid protected data"
        self.path.write_bytes(broken)
        with self.assertRaises(OSError):
            read_settings(self.path)
        self.assertEqual(self.path.read_bytes(), broken)

    def test_failed_write_preserves_original_and_removes_temporary_file(self):
        write_settings(self.path, self.payload)
        original = self.path.read_bytes()
        with patch("cpa_manager.core.settings_store.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                write_settings(self.path, "{}")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_settings_return_defaults_and_malformed_legacy_is_preserved(self):
        self.assertEqual(read_settings(self.path), {})
        self.path.write_bytes(b"{broken")
        with self.assertRaises(OSError):
            read_settings(self.path)
        self.assertEqual(self.path.read_bytes(), b"{broken")
