import hashlib
import json
import os
from pathlib import Path
from cpa_manager.core.paths import ROOT
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from cpa_manager.backends import cli as cli_backend
from cpa_manager.backends import plus as plus_backend
from cpa_manager.core.runtime import VersionCache, discover_servers, windows_architecture


class UnifiedTests(unittest.TestCase):
    def test_manager_release_matches_architecture(self):
        from cpa_manager.backends import manager_update
        for architecture in ("amd64", "arm64"):
            prefix = "/Xunzi229/CPA-Win-Manager/releases/download/v1.2.3/"
            filename = f"CPA-Unified-Manager-v1.2.3-windows-{architecture}.zip"
            page = '<a href="/Xunzi229/CPA-Win-Manager/releases/expanded_assets/v1.2.3">'
            assets = f'<a href="{prefix}{filename}"><a href="{prefix}SHA256SUMS.txt">'
            with patch.object(manager_update, "windows_architecture", return_value=architecture), \
                 patch.object(cli_backend, "read_text", side_effect=(page, assets)):
                release = manager_update.latest_release("")
            self.assertEqual(release[0], "v1.2.3")
            self.assertTrue(release[1].endswith(filename))

    def test_manager_package_verification_and_safe_extraction(self):
        from cpa_manager.backends import manager_update
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / manager_update.EXECUTABLE
            original.write_bytes(b"old executable")
            settings = root / "manager-settings.json"
            settings.write_text("{}", encoding="utf-8")
            package = root / "fixture.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(manager_update.EXECUTABLE, b"new executable")
                archive.writestr("../manager-settings.json", b"do not extract")
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            release = ("v1.2.3", "https://example.test/package.zip", "https://example.test/SHA256SUMS.txt")

            def download(_opener, _url, destination, _report):
                shutil.copyfile(package, destination)
                return digest

            with patch.object(cli_backend, "download", side_effect=download), \
                 patch.object(cli_backend, "read_text", return_value=digest + "  ./package.zip"), \
                 patch.object(manager_update, "windows_architecture", return_value="amd64"), \
                 patch.object(manager_update, "executable_architecture", return_value="amd64"):
                stage = manager_update.prepare_update(root, release, "", lambda *_: None)
                self.assertEqual((stage / manager_update.EXECUTABLE).read_bytes(), b"new executable")
                self.assertEqual(original.read_bytes(), b"old executable")
                self.assertEqual(settings.read_text(), "{}")
                with patch.object(cli_backend, "read_text", return_value="0" * 64 + "  package.zip"):
                    with self.assertRaisesRegex(RuntimeError, "SHA256"):
                        manager_update.prepare_update(root, release, "", lambda *_: None)
                with patch.object(manager_update, "executable_architecture", return_value="arm64"):
                    with self.assertRaisesRegex(RuntimeError, "架构"):
                        manager_update.prepare_update(root, release, "", lambda *_: None)

    def test_manager_update_helper_and_path_guard(self):
        from cpa_manager.backends import manager_update
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / ".manager-update-test"
            stage.mkdir()
            with patch.dict(os.environ, {"PYINSTALLER_RESET_ENVIRONMENT": "0"}), \
                 patch.object(manager_update.subprocess, "Popen") as launch:
                manager_update.launch_update(root / manager_update.EXECUTABLE, stage)
            environment = launch.call_args.kwargs["env"]
            self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertEqual(environment["CPA_UPDATE_TARGET"], str((root / manager_update.EXECUTABLE).resolve()))
            self.assertIn("WaitForExit", (stage / "replace.ps1").read_text(encoding="utf-8-sig"))
            with self.assertRaises(ValueError):
                manager_update.launch_update(root / manager_update.EXECUTABLE, root.parent)

    @unittest.skipUnless(os.name == "nt", "Windows update helper")
    def test_manager_helper_replaces_and_rolls_back(self):
        from cpa_manager.backends import manager_update
        for fail_restart in (False, True):
            with self.subTest(fail_restart=fail_restart), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                executable = root / manager_update.EXECUTABLE
                executable.write_bytes(b"old")
                stage = root / ".manager-update-test"
                stage.mkdir()
                (stage / manager_update.EXECUTABLE).write_bytes(b"new")
                with patch.object(manager_update.subprocess, "Popen") as launch:
                    manager_update.launch_update(executable, stage)
                environment = launch.call_args.kwargs["env"]
                environment["CPA_UPDATE_PID"] = "2147483647"
                environment["CPA_TEST_FAIL"] = "1" if fail_restart else "0"
                runner = stage / "test-helper.ps1"
                runner.write_text("""$script:calls = 0
function Start-Process {
    param($FilePath, $WorkingDirectory, $WindowStyle)
    $script:calls++
    if ($env:CPA_TEST_FAIL -eq '1' -and $script:calls -eq 1) { throw 'Simulated restart failure' }
    $FilePath | Set-Content -LiteralPath (Join-Path $env:CPA_UPDATE_STAGE 'restart.txt')
}
. (Join-Path $env:CPA_UPDATE_STAGE 'replace.ps1')
""", encoding="utf-8-sig")
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(runner)], env=environment,
                    capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(executable.read_bytes(), b"old" if fail_restart else b"new")
                self.assertEqual((stage / "previous.exe").read_bytes(), b"old")
                self.assertTrue((stage / "restart.txt").is_file())
                self.assertEqual((stage / "update-error.log").exists(), fail_restart)

    @unittest.skipUnless(os.name == "nt" and Path(os.environ.get("CPA_TEST_FROZEN_EXE") or ROOT / "CPA-Unified-Manager.exe").is_file(),
                         "Requires a built Windows manager executable")
    def test_frozen_manager_update_resets_deleted_runtime(self):
        from cpa_manager.backends import manager_update
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / manager_update.EXECUTABLE
            fixture = Path(os.environ.get("CPA_TEST_FROZEN_EXE") or (ROOT / manager_update.EXECUTABLE))
            shutil.copyfile(fixture, executable)
            stage = root / ".manager-update-test"
            stage.mkdir()
            shutil.copyfile(executable, stage / manager_update.EXECUTABLE)
            inherited = {"_PYI_ARCHIVE_FILE": str(executable),
                         "_PYI_APPLICATION_HOME_DIR": str(root / "deleted-runtime"),
                         "_PYI_PARENT_PROCESS_LEVEL": "1", "PYINSTALLER_RESET_ENVIRONMENT": "0"}
            with patch.dict(os.environ, inherited), patch.object(manager_update.subprocess, "Popen") as launch:
                manager_update.launch_update(executable, stage)
            environment = launch.call_args.kwargs["env"]
            environment["CPA_UPDATE_PID"] = "2147483647"
            runner = stage / "test-restart.ps1"
            runner.write_text("""function Start-Process {
    param($FilePath, $WorkingDirectory, $WindowStyle)
    $report = Join-Path $env:CPA_UPDATE_STAGE 'smoke.json'
    Microsoft.PowerShell.Management\\Start-Process -FilePath $FilePath -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -ArgumentList @('--smoke-report', ('"' + $report + '"')) -Wait
}
. (Join-Path $env:CPA_UPDATE_STAGE 'replace.ps1')
""", encoding="utf-8-sig")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(runner)], env=environment,
                capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((stage / "update-error.log").exists())
            data = json.loads((stage / "smoke.json").read_text(encoding="utf-8"))
            self.assertTrue(data["frozen"])
            self.assertEqual(data["tabs"], ["CLIProxyAPI", "CPA-Manager-Plus", "免安装软件", "安装向导软件"])
            self.assertTrue((stage / "previous.exe").is_file())

    def test_manager_embedded_version(self):
        from cpa_manager.core import version as app_version
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app-version.json").write_text('{"version":"2.3.4"}', encoding="utf-8")
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "_MEIPASS", directory, create=True):
                self.assertEqual(app_version.current_version(), "2.3.4")

    @unittest.skipUnless(os.name == "nt", "Windows architecture API")
    def test_native_architecture_under_emulation(self):
        kernel = Mock()
        kernel.GetCurrentProcess.return_value = 1
        def report_arm64(_handle, _process, native):
            native._obj.value = 0xAA64
            return 1
        kernel.IsWow64Process2.side_effect = report_arm64
        with patch("ctypes.WinDLL", return_value=kernel):
            self.assertEqual(windows_architecture(), "arm64")

    def test_release_package_matches_windows_architecture(self):
        cases = ((cli_backend, "CLIProxyAPI", "router-for-me/CLIProxyAPI", "aarch64"),
                 (plus_backend, "cpa-manager-plus", "seakee/CPA-Manager-Plus", "arm64"))
        for backend, prefix, repository, arm_asset in cases:
            for architecture, asset_architecture in (("amd64", "amd64"), ("arm64", arm_asset)):
                with self.subTest(project=repository, architecture=architecture):
                    base = f"/{repository}/releases/download/v1.2.3/"
                    assets = "".join(f'<a href="{base}{prefix}_v1.2.3_windows_{name}.zip">' for name in
                                     ("amd64", arm_asset)) + f'<a href="{base}checksums.txt">'
                    page = f'<a href="/{repository}/releases/expanded_assets/v1.2.3">'
                    with patch.object(backend, "windows_architecture", return_value=architecture), \
                         patch.object(backend, "read_text", side_effect=(page, assets)):
                        tag, package, checksum = backend.latest_release(None)
                    self.assertEqual(tag, "v1.2.3")
                    self.assertTrue(package.endswith(f"_windows_{asset_architecture}.zip"))
                    self.assertTrue(checksum.endswith("/checksums.txt"))

    def test_matching_version_replaces_wrong_architecture(self):
        for backend, filename in ((cli_backend, "cli-proxy-api.exe"),
                                  (plus_backend, "cpa-manager-plus.exe")):
            with self.subTest(project=filename), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / filename
                header = bytearray(70)
                header[:2] = b"MZ"
                header[60:64] = (64).to_bytes(4, "little")
                header[64:68] = b"PE\0\0"
                header[68:70] = (0x8664).to_bytes(2, "little")
                executable.write_bytes(header)
                with patch.object(backend, "local_version", return_value="1.2.3"), \
                     patch.object(backend, "latest_release", return_value=("v1.2.3", "unused", "unused")):
                    messages = []
                    with patch("cpa_manager.core.runtime.windows_architecture", return_value="arm64"):
                        backend.update("", lambda _progress, message: messages.append(message),
                                       check_only=True, root=Path(directory))
                    self.assertIn("可更新", messages[-1])
                    messages.clear()
                    with patch("cpa_manager.core.runtime.windows_architecture", return_value="amd64"):
                        backend.update("", lambda _progress, message: messages.append(message),
                                       check_only=True, root=Path(directory))
                    self.assertIn("无需更新", messages[-1])

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_single_instance_across_processes(self):
        from cpa_manager.core.single_instance import SingleInstance
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "CPA-Unified-Manager.exe"
            script = ("from pathlib import Path; from cpa_manager.core.single_instance import SingleInstance; "
                      "instance = SingleInstance(Path(__import__('sys').argv[1])); "
                      "print('first' if instance.is_first else 'duplicate'); instance.close()")

            def launch():
                return subprocess.check_output(
                    [sys.executable, "-c", script, str(executable)],
                    cwd=ROOT, text=True,
                ).strip()

            first = SingleInstance(executable)
            try:
                self.assertTrue(first.is_first)
                self.assertEqual(launch(), "duplicate")
            finally:
                first.close()
            self.assertEqual(launch(), "first")

    def test_modal_position_above_button_and_screen_edges(self):
        from cpa_manager.core.runtime import anchored_popup_position
        self.assertEqual(anchored_popup_position((1300, 400, 90, 30), (500, 280), (0, 0, 1920, 1040)), (890, 112))
        self.assertEqual(anchored_popup_position((1300, 50, 90, 30), (500, 280), (0, 0, 1920, 1040)), (890, 88))
        x, y = anchored_popup_position((-1900, 400, 90, 30), (500, 280), (-1920, 0, 0, 1040))
        self.assertGreaterEqual(x, -1920)
        self.assertLessEqual(x + 500, 0)
        self.assertGreaterEqual(y, 0)

    def test_shared_proxy_migrates_enabled_profile(self):
        from cpa_manager.config import shared_proxy_settings
        profiles = {"cli": {"proxy_enabled": False, "proxy": "http://127.0.0.1:7890"},
                    "plus": {"proxy_enabled": True, "proxy": "http://127.0.0.1:8888"}}
        self.assertEqual(shared_proxy_settings({}, profiles), {"enabled": True, "url": "http://127.0.0.1:8888"})
        saved = {"proxy_settings": {"enabled": False, "url": "http://127.0.0.1:9999"}}
        self.assertEqual(shared_proxy_settings(saved, profiles), saved["proxy_settings"])

    def test_update_badge_version_comparison(self):
        from cpa_manager.config import has_update
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
            with patch("cpa_manager.core.runtime.psutil.process_iter", return_value=[own, other]):
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
        from cpa_manager.ui.pages.project import ProjectPage
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

    def test_manager_auto_check_interval_and_timing(self):
        import time
        from cpa_manager.app import App
        app = Mock(spec=App)
        app.MANAGER_CHECK_INTERVAL = App.MANAGER_CHECK_INTERVAL
        app.manager_check_due = App.manager_check_due.__get__(app, App)
        app.auto_check_manager_update = App.auto_check_manager_update.__get__(app, App)
        app.schedule_next_manager_auto_check = App.schedule_next_manager_auto_check.__get__(app, App)
        app.check_manager_update = Mock()
        app.window = Mock()
        app.manager_busy = False
        app.closing = False

        # 1. Never checked -> check is due
        app.manager_last_check = None
        self.assertTrue(app.manager_check_due())
        app.auto_check_manager_update()
        app.check_manager_update.assert_called_once()

        # 2. Checked 1 hour ago -> check is NOT due
        app.check_manager_update.reset_mock()
        app.manager_last_check = time.time() - 3600
        self.assertFalse(app.manager_check_due())
        app.auto_check_manager_update()
        app.check_manager_update.assert_not_called()
        app.window.after.assert_called()

        # 3. Checked 4.9 hours ago -> check is NOT due
        app.manager_last_check = time.time() - int(4.9 * 3600)
        self.assertFalse(app.manager_check_due())

        # 4. Checked 5 hours ago -> check is due
        app.check_manager_update.reset_mock()
        app.manager_last_check = time.time() - 5 * 3600 - 1
        self.assertTrue(app.manager_check_due())
        app.auto_check_manager_update()
        app.check_manager_update.assert_called_once()

        # 5. Checked 10 hours ago -> check is due
        app.check_manager_update.reset_mock()
        app.manager_last_check = time.time() - 10 * 3600
        self.assertTrue(app.manager_check_due())
        app.auto_check_manager_update()
        app.check_manager_update.assert_called_once()

    def test_manager_update_poll_records_timestamp_and_saves(self):
        import queue
        import time
        from cpa_manager.app import App
        app = Mock(spec=App)
        app.manager_events = queue.Queue()
        app.manager_events.put(("release", ("v2.0.0", "https://example.com/a.zip", "https://example.com/s.txt")))
        app.manager_version = "1.0.0"
        app.manager_busy = True
        app.manager_status = Mock()
        app.refresh_manager_controls = Mock()
        app.save = Mock()
        app.schedule_next_manager_auto_check = Mock()
        app.window = Mock()
        app.poll_manager_update = App.poll_manager_update.__get__(app, App)

        now_before = time.time()
        app.poll_manager_update()
        now_after = time.time()

        self.assertFalse(app.manager_busy)
        self.assertEqual(app.manager_release, ("v2.0.0", "https://example.com/a.zip", "https://example.com/s.txt"))
        self.assertIsNotNone(app.manager_last_check)
        self.assertGreaterEqual(app.manager_last_check, now_before)
        self.assertLessEqual(app.manager_last_check, now_after)
        app.save.assert_called_once()
        app.schedule_next_manager_auto_check.assert_called_once()

    def test_manager_last_check_and_release_persisted_in_settings(self):
        from types import SimpleNamespace
        from cpa_manager.app import App
        from cpa_manager.backends.release_cache import ReleaseCache
        from cpa_manager.core.settings_store import read_settings
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            app = SimpleNamespace(profiles={}, proxy_settings={}, custom_profiles=[], installer_profiles=[],
                                  installer_download_directory=directory, release_cache=ReleaseCache(),
                                  settings_file=settings_path, manager_last_check=1700000000.5,
                                  manager_release=("v1.2.3", "https://url/zip", "https://url/sums"))
            App.save(app)
            saved = read_settings(settings_path)
            self.assertEqual(saved["manager_last_check"], 1700000000.5)
            self.assertEqual(saved["manager_release"], ["v1.2.3", "https://url/zip", "https://url/sums"])

    def test_manager_progress_bar_and_async_controls(self):
        import queue
        from cpa_manager.app import App
        app = Mock(spec=App)
        app.manager_events = queue.Queue()
        app.manager_busy = True
        app.manager_status = Mock()
        app.manager_release = ("v2.0.0", "zip", "sums")
        app.manager_version = "1.0.0"
        app.manager_progress = Mock()
        app.manager_progress.winfo_exists.return_value = True
        app.manager_progress.__getitem__ = Mock(return_value="determinate")
        app.manager_check = Mock()
        app.manager_check.winfo_exists.return_value = True
        app.manager_install = Mock()
        app.manager_install.winfo_exists.return_value = True
        app.save = Mock()
        app.schedule_next_manager_auto_check = Mock()
        app.window = Mock()
        app.set_manager_progress = App.set_manager_progress.__get__(app, App)
        app.refresh_manager_controls = App.refresh_manager_controls.__get__(app, App)
        app.poll_manager_update = App.poll_manager_update.__get__(app, App)

        # 1. Busy refresh updates buttons and packs progress bar
        app.update_actions = Mock()
        app.update_actions.winfo_exists.return_value = True
        app.manager_progress.winfo_ismapped.return_value = False
        app.refresh_manager_controls()
        app.manager_check.configure.assert_called_with(state="disabled")
        app.manager_install.configure.assert_any_call(text="正在更新…")
        app.manager_progress.pack.assert_called_once_with(fill="x", pady=(0, 8), before=app.update_actions)

        # Idle refresh hides progress bar
        app.manager_busy = False
        app.manager_progress.winfo_ismapped.return_value = True
        app.refresh_manager_controls()
        app.manager_progress.pack_forget.assert_called_once()
        app.manager_busy = True
        app.manager_progress.winfo_ismapped.return_value = False

        # 2. Indeterminate progress event
        app.manager_events.put(("progress", (None, "正在获取校验信息…")))
        app.poll_manager_update()
        self.assertIsNone(app.manager_progress_value)
        app.manager_status.set.assert_called_with("正在获取校验信息…")
        app.manager_progress.configure.assert_called_with(mode="indeterminate")
        app.manager_progress.start.assert_called_with(15)

        # 3. Numeric progress event
        app.manager_events.put(("progress", (50.0, "正在下载：5.0 MB / 10.0 MB")))
        app.poll_manager_update()
        self.assertEqual(app.manager_progress_value, 50.0)
        app.manager_status.set.assert_called_with("正在下载：5.0 MB / 10.0 MB")
        app.manager_progress.stop.assert_called()
        app.manager_progress.configure.assert_called_with(mode="determinate", value=50.0)

        # 4. Error resets progress to 0
        app.manager_events.put(("error", "网络超时"))
        app.poll_manager_update()
        self.assertFalse(app.manager_busy)
        self.assertEqual(app.manager_progress_value, 0)


if __name__ == "__main__":
    unittest.main()
