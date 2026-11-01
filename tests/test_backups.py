"""Backup cleanup preserves live files and never follows redirected directories."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from cpa_manager.core.backups import backup_directories, clear_backups, backup_button_text, portable_backup_prefix
from cpa_manager.core.download import DownloadControl
from cpa_manager.core.locking import update_lock


class BackupTests(unittest.TestCase):
    def test_shared_directory_cleanup_is_scoped_to_normalized_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            one = "https://github.com/owner/one"
            two = "https://github.com/owner/two"
            first = root / (portable_backup_prefix(one) + "a" * 32)
            second = root / (portable_backup_prefix(two) + "b" * 32)
            legacy = root / (".install-backup-" + "c" * 32)
            for backup in (first, second, legacy):
                backup.mkdir()
                (backup / "keep.exe").write_bytes(b"backup")
            alias = "https://github.com/OWNER/ONE/releases/latest"
            self.assertEqual(backup_button_text(root, "portable", alias), "清空安装备份 (1)")
            self.assertEqual(clear_backups(root, "portable", Mock(), repository=alias), 1)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(legacy.exists())
            self.assertEqual(backup_directories(root, "portable", ""), [])
            self.assertEqual(clear_backups(root, "portable", Mock(), legacy_only=True), 1)
            self.assertFalse(legacy.exists())
            self.assertTrue(second.exists())

    def test_cleanup_removes_only_generated_backups(self):
        for kind in ("portable", "project"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                parent = root if kind == "portable" else root / ".update-backups"
                name = ".install-backup-" + "a" * 32 if kind == "portable" else "20261002-120000-1234abcd"
                backup = parent / name
                backup.mkdir(parents=True)
                (backup / "old.exe").write_bytes(b"old")
                unknown = parent / "install-backup-user"
                unknown.mkdir()
                live = root / "app.exe"
                live.write_bytes(b"live")
                (root / "config.yaml").write_text("keep")
                self.assertEqual(backup_directories(root, kind), [backup])
                self.assertEqual(backup_button_text(root, kind), "清空安装备份 (1)")
                self.assertEqual(clear_backups(root, kind, Mock()), 1)
                self.assertEqual(backup_button_text(root, kind), "清空安装备份")
                self.assertTrue(unknown.is_dir())
                self.assertEqual(live.read_bytes(), b"live")
                self.assertEqual((root / "config.yaml").read_text(), "keep")
                self.assertEqual(clear_backups(root, kind, Mock()), 0)

    def test_install_lock_prevents_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / (".install-backup-" + "b" * 32)
            backup.mkdir()
            with update_lock(root), self.assertRaises(RuntimeError):
                clear_backups(root, "portable", Mock())
            self.assertTrue(backup.exists())

    def test_failed_delete_remains_available_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / (".install-backup-" + "c" * 32)
            backup.mkdir()
            with patch("cpa_manager.core.backups.shutil.rmtree", side_effect=PermissionError("locked")), self.assertRaisesRegex(RuntimeError, "部分清理失败"):
                clear_backups(directory, "portable", Mock())
            self.assertTrue(backup.exists())

    def test_junction_backup_does_not_delete_external_files(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external:
            root = Path(directory)
            backup = root / (".install-backup-" + "d" * 32)
            sentinel = Path(external) / "keep.txt"
            sentinel.write_text("keep")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(backup), external], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                self.assertEqual(backup_directories(root, "portable"), [])
                self.assertEqual(clear_backups(root, "portable", Mock()), 0)
                self.assertEqual(sentinel.read_text(), "keep")
            finally:
                backup.rmdir()

    def test_cancelled_cleanup_does_not_remove_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / (".install-backup-" + "e" * 32)
            backup.mkdir()
            control = DownloadControl()
            control.request_cancel()
            from cpa_manager.core.download import DownloadCancelled
            with self.assertRaises(DownloadCancelled):
                clear_backups(directory, "portable", Mock(), control)
            self.assertTrue(backup.exists())
