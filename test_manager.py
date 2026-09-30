import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

import cli_backend
import plus_backend
from runtime_utils import VersionCache, discover_servers


class UnifiedTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_single_instance_across_processes(self):
        from single_instance import SingleInstance
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "CPA-Unified-Manager.exe"
            script = ("from pathlib import Path; from single_instance import SingleInstance; "
                      "instance = SingleInstance(Path(__import__('sys').argv[1])); "
                      "print('first' if instance.is_first else 'duplicate'); instance.close()")

            def launch():
                return subprocess.check_output(
                    [sys.executable, "-c", script, str(executable)],
                    cwd=Path(__file__).parent, text=True,
                ).strip()

            first = SingleInstance(executable)
            try:
                self.assertTrue(first.is_first)
                self.assertEqual(launch(), "duplicate")
            finally:
                first.close()
            self.assertEqual(launch(), "first")

    def test_modal_position_above_button_and_screen_edges(self):
        from runtime_utils import anchored_popup_position
        self.assertEqual(anchored_popup_position((1300, 400, 90, 30), (500, 280), (0, 0, 1920, 1040)), (890, 112))
        self.assertEqual(anchored_popup_position((1300, 50, 90, 30), (500, 280), (0, 0, 1920, 1040)), (890, 88))
        x, y = anchored_popup_position((-1900, 400, 90, 30), (500, 280), (-1920, 0, 0, 1040))
        self.assertGreaterEqual(x, -1920)
        self.assertLessEqual(x + 500, 0)
        self.assertGreaterEqual(y, 0)

    def test_shared_proxy_migrates_enabled_profile(self):
        from manager import shared_proxy_settings
        profiles = {"cli": {"proxy_enabled": False, "proxy": "http://127.0.0.1:7890"},
                    "plus": {"proxy_enabled": True, "proxy": "http://127.0.0.1:8888"}}
        self.assertEqual(shared_proxy_settings({}, profiles), {"enabled": True, "url": "http://127.0.0.1:8888"})
        saved = {"proxy_settings": {"enabled": False, "url": "http://127.0.0.1:9999"}}
        self.assertEqual(shared_proxy_settings(saved, profiles), saved["proxy_settings"])

    def test_update_badge_version_comparison(self):
        from manager import has_update
        self.assertTrue(has_update("8.0.3", "v8.0.4"))
        self.assertFalse(has_update("8.0.4", "v8.0.4"))
        self.assertFalse(has_update("8.0.5", "v8.0.4"))
        self.assertFalse(has_update(None, "v8.0.4"))
        self.assertFalse(has_update("dev", "v8.0.4"))
        self.assertFalse(has_update("8.0.3", None))

    def test_version_cache_rechecks_only_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "server.exe"
            executable.write_bytes(b"version1")
            reader = Mock(return_value="1.0.0")
            cache = VersionCache()
            self.assertEqual(cache.read(root, executable.name, reader), "1.0.0")
            self.assertEqual(cache.read(root, executable.name, reader), "1.0.0")
            self.assertEqual(reader.call_count, 1)
            executable.write_bytes(b"version2-new-content")
            reader.return_value = "2.0.0"
            self.assertEqual(cache.read(root, executable.name, reader), "2.0.0")
            self.assertEqual(reader.call_count, 2)

    def test_native_process_discovery_filters_other_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own = Mock(pid=123, info={"pid": 123, "name": "server.exe"})
            own.exe.return_value = str(root / "server.exe")
            own.cmdline.return_value = [str(root / "server.exe"), "--config", "path with spaces.json"]
            own.environ.return_value = {"HTTP_ADDR": "127.0.0.1:1234"}
            other = Mock(pid=456, info={"pid": 456, "name": "server.exe"})
            other.exe.return_value = str(root / "other" / "server.exe")
            with patch("runtime_utils.psutil.process_iter", return_value=[own, other]):
                servers = discover_servers(root, "server.exe", True)
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0]["ProcessId"], 123)
            self.assertIn('"path with spaces.json"', servers[0]["CommandLine"])

    def test_old_log_does_not_replace_manually_imported_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "manager-service.log"
            log.write_text("2020/01/01 00:00:00 CPA Manager Plus admin key generated: cpamp_old\n")
            plus_backend.save_admin_key(root, "cpamp_new_manual")
            with log.open("a") as output:
                output.write("new unrelated service output\n")
            timestamp = (root / "manager-admin-key.dpapi").stat().st_mtime + 1
            os.utime(log, (timestamp, timestamp))
            self.assertEqual(plus_backend.find_admin_key(root), "cpamp_new_manual")

    def test_key_failure_does_not_break_service_status(self):
        from manager import ProjectPage
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "server.exe").touch()
            page = ProjectPage.__new__(ProjectPage)
            page.key, page.executable = "plus", "server.exe"
            page.version_cache = VersionCache()
            page.backend = Mock()
            page.backend.running_servers.return_value = [{"ProcessId": 123}]
            page.backend.server_status.return_value = (True, "运行中")
            page.backend.local_version.return_value = "1.0.0"
            page.backend.find_admin_key.side_effect = OSError("cannot decrypt")
            running, state, version, key, error = page.snapshot(root)
            self.assertTrue(running)
            self.assertEqual(state, "运行中")
            self.assertEqual(version, "1.0.0")
            self.assertTrue(error)

    def test_fresh_install_and_existing_config_preserved(self):
        for backend, exe, config in ((cli_backend, "cli-proxy-api.exe", "config.yaml"),
                                     (plus_backend, "cpa-manager-plus.exe", "config.json")):
            for existing in (False, True):
                with self.subTest(project=exe, existing=existing), tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    target = base / "new-project"
                    target.mkdir()
                    if existing:
                        (target / config).write_text("user-custom-config")
                    (target / "data").mkdir()
                    (target / "data" / "data.key").write_text("keep-user-key")
                    archive = base / "release.zip"
                    with zipfile.ZipFile(archive, "w") as package:
                        package.writestr("release/" + exe, b"test executable")
                        package.writestr("release/README.md", "documentation")
                        package.writestr("release/" + config, "do not overwrite")
                        package.writestr("release/data/data.key", "do not overwrite")
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    def download(opener, url, destination, report):
                        shutil.copyfile(archive, destination)
                        return digest
                    with patch.object(backend, "latest_release", return_value=("v99.0.0", "https://example.org/release.zip", "https://example.org/checksums.txt")), \
                         patch.object(backend, "local_version", return_value=None), \
                         patch.object(backend, "read_text", return_value=digest + "  ./release.zip\n"), \
                         patch.object(backend, "download", side_effect=download), \
                         patch.object(backend, "running_servers", return_value=[]), \
                         patch.object(backend, "restart_server") as restart:
                        backend.update("", lambda *args: None, root=target)
                        restart.assert_not_called()
                    self.assertTrue((target / exe).is_file())
                    self.assertEqual((target / "data" / "data.key").read_text(), "keep-user-key")
                    if existing:
                        self.assertEqual((target / config).read_text(), "user-custom-config")
                    else:
                        host, port = backend.server_endpoint(target)
                        self.assertEqual(host, "127.0.0.1")
                        self.assertIn(port, (8317, 18317))

    def test_cli_v8_config(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "config.yaml").write_text("server:\n  host: 127.0.0.1\n  port: 19001\n")
            self.assertEqual(cli_backend.server_endpoint(target), ("127.0.0.1", 19001))

    def test_latest_versions_skip_install(self):
        for backend in (cli_backend, plus_backend):
            with self.subTest(project=backend.REPO), patch.object(backend, "local_version", return_value="1.2.3"), \
                 patch.object(backend, "latest_release", return_value=("v1.2.3", "unused", "unused")), \
                 patch.object(backend, "download") as download:
                backend.update("", lambda *args: None)
                download.assert_not_called()

    def test_uninstalled_check_does_not_create_directory(self):
        for backend in (cli_backend, plus_backend):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "missing"
                with patch.object(backend, "latest_release", return_value=("v1.0.0", "unused", "unused")):
                    backend.update("", lambda *args: None, True, root=target)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
