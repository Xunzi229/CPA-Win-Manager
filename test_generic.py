import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import generic_backend as backend


class GenericInstallTests(unittest.TestCase):
    def test_repository_normalization_and_rejection(self):
        for suffix in ("", ".git", "/releases/latest", "/releases/tag/v1.0.0"):
            self.assertEqual(backend.repository("https://github.com/owner/tool" + suffix),
                             "https://github.com/owner/tool")
        for value in ("https://github.com.evil.test/owner/tool", "file:///owner/tool",
                      "https://github.com/owner/..", "https://github.com/owner"):
            with self.assertRaises(ValueError):
                backend.repository(value)

    def test_release_and_architecture_selection(self):
        repo = "https://github.com/owner/tool"
        data = {"tag_name": "v2.0.0", "assets": [
            {"name": name, "browser_download_url": repo + "/releases/download/v2.0.0/" + name}
            for name in ("tool-linux-amd64.zip", "tool-windows-arm64.zip", "tool-windows-amd64.zip", "setup.msi")]}
        with patch.object(backend.cli_backend, "read_text", return_value=json.dumps(data)):
            release = backend.releases(repo + "/releases/latest", "")
        with patch.object(backend, "windows_architecture", return_value="amd64"):
            self.assertEqual(backend.candidates(release)[0]["name"], "tool-windows-amd64.zip")
            self.assertEqual(backend.candidates(release, "安装器")[0]["name"], "setup.msi")

    def package(self, base, entries, digest=None):
        archive = base / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name, content in entries:
                package.writestr(name, content)
        value = hashlib.sha256(archive.read_bytes()).hexdigest()
        asset = {"name": "tool-v2.zip", "url": "https://github.com/owner/tool/releases/download/v2/tool.zip",
                 "digest": digest or "sha256:" + value}
        release = {"repository": "https://github.com/owner/tool", "tag": "v2", "assets": [asset]}
        def download(_url, destination, _report, **kwargs):
            shutil.copyfile(archive, destination)
            return value
        return release, asset, patch.object(backend.transfer, "fetch", side_effect=download)

    def test_install_preserve_metadata_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "software"
            (root / "data").mkdir(parents=True)
            (root / "tool.exe").write_text("old")
            (root / "config.yaml").write_text("user config")
            (root / "data" / "db").write_text("user data")
            (root / "extra").write_text("keep")
            release, asset, downloading = self.package(base, [
                ("wrapper/tool.exe", "new"), ("wrapper/config.yaml", "default"),
                ("wrapper/data/db", "empty"), ("wrapper/data/new", "seed")])
            with downloading:
                backend.install(release, asset, root, "config.yaml;data", "", lambda *_: None)
            self.assertEqual((root / "tool.exe").read_text(), "new")
            self.assertEqual((root / "config.yaml").read_text(), "user config")
            self.assertEqual((root / "data" / "db").read_text(), "user data")
            self.assertEqual((root / "data" / "new").read_text(), "seed")
            self.assertEqual((root / "extra").read_text(), "keep")
            self.assertEqual(backend.local_version(root, release["repository"]), "v2")
            self.assertIsNone(backend.local_version(root, "https://github.com/other/tool"))
            self.assertEqual(next(root.glob(".install-backup-*/tool.exe")).read_text(), "old")

    def test_replacement_failure_restores_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "software"
            root.mkdir()
            (root / "a.exe").write_text("old")
            release, asset, downloading = self.package(base, [("a.exe", "new"), ("nested/new.txt", "new"), ("z.exe", "new")])
            replace = backend.os.replace
            def locked(source, destination):
                if Path(destination).name == "z.exe":
                    raise PermissionError("file is locked")
                return replace(source, destination)
            with downloading, patch.object(backend.os, "replace", side_effect=locked):
                with self.assertRaisesRegex(RuntimeError, "已恢复"):
                    backend.install(release, asset, root, "", "", lambda *_: None)
            self.assertEqual((root / "a.exe").read_text(), "old")
            self.assertFalse((root / "nested").exists())
            self.assertFalse((root / backend.METADATA).exists())

    def test_unsafe_packages_and_checksum_do_not_replace(self):
        for entries, digest in (([("../escape", "bad")], None),
                                ([("A.exe", "1"), ("a.exe", "2")], None),
                                ([("tool.exe", "new")], "sha256:" + "0" * 64)):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "software"
                root.mkdir()
                (root / "tool.exe").write_text("old")
                release, asset, downloading = self.package(base, entries, digest)
                with downloading, self.assertRaises((RuntimeError, ValueError)):
                    backend.install(release, asset, root, "", "", lambda *_: None)
                self.assertEqual((root / "tool.exe").read_text(), "old")
                self.assertFalse((base / "escape").exists())

    def test_installer_download_does_not_execute_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "setup.exe").write_bytes(b"old")
            digest = hashlib.sha256(b"new").hexdigest()
            asset = {"name": "setup.exe", "url": "https://github.com/owner/tool/releases/download/v2/setup.exe", "digest": "sha256:" + digest}
            release = {"assets": [asset]}
            def download(_url, destination, _report, **kwargs):
                destination.write_bytes(b"new")
                return digest
            with patch.object(backend.transfer, "fetch", side_effect=download):
                path = backend.download_installer(release, asset, root, "", lambda *_: None)
            self.assertEqual(path.read_bytes(), b"new")
            self.assertEqual((root / "setup.exe").read_bytes(), b"old")

    def test_empty_preserve_overwrites_only_package_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "software"
            root.mkdir()
            (root / "config.yaml").write_text("old")
            (root / "extra.txt").write_text("unrelated")
            release, asset, downloading = self.package(base, [("config.yaml", "new")])
            with downloading:
                backend.install(release, asset, root, "", "", lambda *_: None)
            self.assertEqual((root / "config.yaml").read_text(), "new")
            self.assertEqual((root / "extra.txt").read_text(), "unrelated")

    def test_cancelled_download_does_not_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "software"
            root.mkdir()
            (root / "tool.exe").write_text("old")
            release, asset, _ = self.package(base, [("tool.exe", "new")])
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(backend.transfer.DownloadCancelled):
                backend.install(release, asset, root, "", "", lambda *_: None, cancel)
            self.assertEqual((root / "tool.exe").read_text(), "old")
            self.assertFalse((root / backend.METADATA).exists())


if __name__ == "__main__":
    unittest.main()
