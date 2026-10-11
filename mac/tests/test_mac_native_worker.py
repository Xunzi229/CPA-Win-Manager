"""Native button dispatch tests; no user's apps or downloads are touched."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpa_mac import native_worker as worker
from cpa_mac.backends import github, service, software
from cpa_mac.config import PROJECTS


class NativeWorkerTests(unittest.TestCase):
    def test_management_url_uses_current_config_and_refuses_stopped_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = self.project_request(root, "management_url")
            plus = root / "plus"
            plus.mkdir()
            (plus / "config.json").write_text('{"httpAddr":"[::1]:19317"}')
            with patch.object(service, "running_processes", return_value=[object()]), \
                 patch.object(service.socket, "create_connection") as connect:
                self.assertEqual(worker.execute(request, lambda event: None)["url"], "http://[::1]:19317/management.html")
                connect.assert_called_once_with(("::1", 19317), timeout=3)
            with patch.object(service, "running_processes", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "未运行"):
                    worker.execute(request, lambda event: None)
            with patch.object(service, "running_processes", return_value=[object()]), \
                 patch.object(service.socket, "create_connection", side_effect=ConnectionRefusedError):
                with self.assertRaisesRegex(RuntimeError, "尚未监听"):
                    worker.execute(request, lambda event: None)
            (plus / "config.json").write_text('{"httpAddr":"user@host/path:19317"}')
            with patch.object(service, "running_processes", return_value=[object()]):
                with self.assertRaisesRegex(RuntimeError, "监听地址"):
                    worker.execute(request, lambda event: None)

    def test_web_key_reader_returns_the_same_status(self):
        from types import SimpleNamespace
        from cpa_mac.webapp import Handler
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service.save_admin_key(root, "fixture-web-key")
            store = SimpleNamespace(data={"plus": {"directory": str(root)}})
            handler = object.__new__(Handler)
            result = handler._project("plus", "read-key", {}, store)
            self.assertEqual(result["admin_key"], "fixture-web-key")
            self.assertEqual(result["directory"], str(root))
            with self.assertRaises(ValueError):
                handler._project("cli", "read-key", {}, store)

    def project_request(self, root, action):
        request = self.request(root)
        request.update(scope="project", action=action, key="plus", body={})
        request["config"].update(plus_directory=str(root / "plus"), cli_directory=str(root / "cli"))
        return request

    def test_saved_key_is_read_back_without_changing_service_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = self.project_request(root, "save_key")
            plus = root / "plus"
            plus.mkdir()
            database = plus / "data" / "usage.sqlite"
            database.parent.mkdir()
            database.write_bytes(b"fixture-credential-digest")
            request["body"] = {"key": "fixture-key"}
            events = []
            result = worker.execute(request, events.append)
            self.assertEqual(result["admin_key"], "fixture-key")
            self.assertEqual(database.read_bytes(), b"fixture-credential-digest")
            self.assertEqual((plus / "manager-admin-key.txt").stat().st_mode & 0o777, 0o600)
            request["action"] = "read_key"
            self.assertEqual(worker.execute(request, events.append)["admin_key"], "fixture-key")
            self.assertNotIn("fixture-key", json.dumps(events))

    def test_key_is_captured_from_native_service_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = self.project_request(root, "read_key")
            log = root / "plus" / "manager-service.log"
            log.parent.mkdir()
            log.write_text("2026/10/11 12:00:00 CPA Manager Plus admin key generated: fixture-generated\n")
            result = worker.execute(request, lambda event: None)
            self.assertEqual(result["admin_key"], "fixture-generated")
            self.assertEqual(service.find_admin_key(log.parent), "fixture-generated")

    def test_missing_key_and_read_errors_do_not_break_project_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = self.project_request(root, "read_key")
            result = worker.execute(request, lambda event: None)
            self.assertEqual(result["admin_key"], "")
            self.assertIn("摘要", result["admin_key_note"])
            with patch.object(service, "find_admin_key", side_effect=PermissionError("fixture")):
                result = worker.project_status(request["config"])
                self.assertEqual(result["plus"]["admin_key"], "")
                self.assertIn("PermissionError", result["plus"]["admin_key_note"])
                self.assertIn("cli", result)

    def request(self, root, action="check", mode="package"):
        return {"scope": "software", "action": action, "mode": mode, "source_root": str(root / "manager"),
                "body": {"id": "test", "force": True}, "config": {
                    "proxy_enabled": False, "proxy": "", "prerelease": False, "download_workers": 4,
                    "download_directory": str(root / "downloads"), "portable": [], "packages": [{
                        "id": "test", "name": "test", "repository": "example/test", "directory": str(root / "tool"),
                        "preserve": "config", "latest": "", "local": "1.0.0", "update": True}]}}

    def test_package_check_selects_mac_asset_and_emits_record(self):
        with tempfile.TemporaryDirectory() as folder:
            request = self.request(Path(folder))
            events = []
            release = {"tag": "v2.0.0", "assets": [{"name": "tool-windows-x64.exe"}, {"name": "tool-macos-arm64.dmg"}]}
            with patch.object(github, "fetch_latest", return_value=release), patch.object(worker, "host_architecture", return_value="arm64"):
                worker.execute(request, events.append)
            record = next(event for event in events if event["type"] == "record")
            self.assertEqual(record["mode"], "package")
            self.assertEqual(record["value"]["asset"], "tool-macos-arm64.dmg")
            self.assertTrue(record["value"]["update"])

    def test_download_all_does_not_open_installers_and_single_install_does(self):
        with tempfile.TemporaryDirectory() as folder:
            request = self.request(Path(folder), "upgrade_all")
            request["config"]["packages"][0].update(latest="2.0.0", update=False)
            release = {"tag": "2.0.0", "assets": [{"name": "tool-macos.dmg"}]}
            with patch.object(github, "fetch_latest", return_value=release), patch.object(software, "install_package") as install:
                worker.execute(request, lambda event: None)
                self.assertFalse(install.call_args.kwargs["open_installer"])
                self.assertIsInstance(install.call_args.args[5], threading.Event)
                request["action"] = "install"
                worker.execute(request, lambda event: None)
                self.assertTrue(install.call_args.kwargs["open_installer"])

    def test_cleanup_preserves_untracked_modified_symlink_and_other_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            download = root / "downloads"
            download.mkdir()
            entries = []
            for name in ["managed.dmg", "changed.dmg", "personal.dmg"]:
                path = download / name
                path.write_bytes(b"original")
                if name != "personal.dmg":
                    entries.append({"path": str(path), "sha256": github.file_sha256(path)})
            (download / "changed.dmg").write_bytes(b"modified")
            other = root / "other.dmg"
            other.write_bytes(b"original")
            link = download / "link.dmg"
            link.symlink_to(other)
            entries.extend({"path": str(path), "sha256": github.file_sha256(path)} for path in [other, link])
            result = worker.clean_downloads(entries, download, lambda progress, line: None)
            self.assertEqual(result["deleted"], 1)
            for path in [other, link, download / "changed.dmg", download / "personal.dmg"]:
                self.assertTrue(path.exists())

    def test_backup_cleanup_only_recognizes_manager_backup_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = self.request(root, "clear_backups", "portable")
            request["config"]["portable"] = request["config"]["packages"]
            target = root / "tool"
            target.mkdir()
            backup = target / (".install-backup-" + "a" * 32)
            backup.mkdir()
            unrelated = target / ".install-backup-personal"
            unrelated.mkdir()
            result = worker.execute(request, lambda event: None)
            self.assertEqual(result["deleted"], 1)
            self.assertTrue(unrelated.is_dir())

    def test_missing_download_entry_is_removed_without_inventing_deleted_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder).resolve() / "missing.dmg")
            result = worker.clean_downloads([{"path": path, "sha256": "none"}], folder, lambda *args: None)
            self.assertEqual(result["deleted"], 0)
            self.assertEqual(result["removed_paths"], [path])

    def test_dangerous_paths_and_attachments_rejected(self):
        for directory in ["", "/", str(Path.home()), "relative"]:
            with self.assertRaises(ValueError):
                worker.safe_root(directory, "/tmp/manager")
        for name in ["", "../test.dmg", "/tmp/test.dmg", "..", "bad\\test.dmg"]:
            with self.assertRaises(ValueError):
                worker.asset_path("/tmp/downloads", name)
        with self.assertRaises(ValueError):
            worker.trash_app({"installed_dir": "/System/Applications/Calculator.app"}, "/tmp/manager")

    def test_package_download_only_mode_writes_file_and_registers_it(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            def download(release, asset, path, *args, **kwargs):
                Path(path).write_bytes(b"download")
            with patch.object(github, "expected_sha256", return_value=None), patch.object(github, "download_verified", side_effect=download), patch.object(subprocess, "run") as opened:
                path = software.install_package({"assets": []}, {"name": "test.dmg", "size": 8}, folder, "", lambda *args: None,
                                                open_installer=False, downloaded=events.append)
                self.assertEqual(path.read_bytes(), b"download")
                self.assertEqual(events, [path])
                opened.assert_not_called()

    def test_cancelled_task_never_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            cancel = threading.Event()
            cancel.set()
            with patch.object(github, "fetch_latest") as fetch:
                with self.assertRaisesRegex(RuntimeError, "取消"):
                    worker.execute(self.request(Path(folder)), lambda event: None, cancel)
                fetch.assert_not_called()

    def test_plus_version_pattern_without_capture_group(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / PROJECTS["plus"]["binary"]).touch()
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "version v1.5.14", "")):
                self.assertEqual(service.local_version(folder, PROJECTS["plus"]), "v1.5.14")

    def test_package_check_supports_mac_zip_but_rejects_windows_zip(self):
        assets = [{"name": "tool-windows-arm64.zip"}, {"name": "tool-macos-arm64.zip"}]
        self.assertEqual(github.recommend(assets, "package", "arm64")["name"], "tool-macos-arm64.zip")
        self.assertIsNone(github.recommend(assets[:1], "package", "arm64"))
        self.assertEqual(github.recommend([{"name": "AltTab-10.0.0.zip"}], "package", "arm64")["name"], "AltTab-10.0.0.zip")

    def test_release_web_fallback_uses_html_accept_header_and_same_proxy(self):
        html = '<section><a href="/example/tool/releases/tag/v2.0.0">release</a></section>'
        with patch.object(github, "network") as network, patch.object(github, "read_text", return_value=html), patch.object(github, "scrape_expanded_assets", return_value=[{"name": "tool-macos.dmg"}]):
            release = github.scrape_latest_release("example/tool", "http://localhost:7890")
            network.assert_called_once_with("http://localhost:7890", accept="text/html")
            self.assertEqual(release["tag"], "v2.0.0")

    def test_unknown_action_and_missing_record_do_not_report_success(self):
        with tempfile.TemporaryDirectory() as folder:
            request = self.request(Path(folder), "unknown")
            with self.assertRaisesRegex(ValueError, "不支持"):
                worker.execute(request, lambda event: None)
            request["body"]["id"] = "missing"
            with self.assertRaisesRegex(ValueError, "记录"):
                worker.execute(request, lambda event: None)


if __name__ == "__main__":
    unittest.main()
