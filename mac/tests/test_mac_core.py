"""Tests for macOS asset selection, archives and settings."""
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpa_mac.backends import github, service, software
from cpa_mac.config import MAC_ROOT, PROJECTS, already_current, has_update, version_key
from cpa_mac.core.archive import extract_archive
from cpa_mac.core import settings as settings_store


class VersionTests(unittest.TestCase):
    def test_orders_releases(self):
        self.assertLess(version_key("v1.2.3"), version_key("1.2.4"))
        self.assertLess(version_key("1.2.3-rc.1"), version_key("1.2.3"))
        self.assertIsNone(version_key("latest"))
        self.assertTrue(has_update("1.0.0", "1.0.1"))
        self.assertFalse(has_update("1.0.1", "1.0.1"))
        self.assertFalse(has_update(None, "1.0.1"))
        self.assertTrue(already_current("v8.0.22", "8.0.22"))


class AssetTests(unittest.TestCase):
    def test_project_assets(self):
        cli = [{"name": "CLIProxyAPI_8.0.22_darwin_aarch64.tar.gz"},
               {"name": "CLIProxyAPI_8.0.22_darwin_amd64.tar.gz"},
               {"name": "CLIProxyAPI_8.0.22_windows_amd64.zip"},
               {"name": "checksums.txt"}]
        chosen = github.choose_project_asset(cli, PROJECTS["cli"], "arm64")
        self.assertEqual(chosen["name"], "CLIProxyAPI_8.0.22_darwin_aarch64.tar.gz")
        plus = [{"name": "cpa-manager-plus_v1.14.4_darwin_arm64.tar.gz"},
                {"name": "cpa-manager-plus_v1.14.4_darwin_amd64.tar.gz"},
                {"name": "cpa-manager-plus_v1.14.4_windows_arm64.zip"}]
        chosen = github.choose_project_asset(plus, PROJECTS["plus"], "arm64")
        self.assertEqual(chosen["name"], "cpa-manager-plus_v1.14.4_darwin_arm64.tar.gz")

    def test_portable_and_package_selection(self):
        assets = [
            {"name": "fd-v10.5.0-aarch64-apple-darwin.tar.gz"},
            {"name": "fd-v10.5.0-x86_64-apple-darwin.tar.gz"},
            {"name": "fd-v10.5.0-x86_64-unknown-linux-gnu.tar.gz"},
            {"name": "ripgrep-15.2.0-aarch64-apple-darwin.tar.gz.sha256"},
            {"name": "Ollama.dmg"},
            {"name": "ollama-darwin.tgz"},
            {"name": "KeePassXC-2.7.12-arm64.dmg"},
            {"name": "KeePassXC-2.7.12-x86_64.dmg"},
            {"name": "KeePassXC-2.7.12-arm64.dmg.sig"},
        ]
        self.assertEqual(github.recommend(assets[:4], "portable", "arm64")["name"],
                         "fd-v10.5.0-aarch64-apple-darwin.tar.gz")
        self.assertEqual(github.recommend(assets, "package", "arm64")["name"], "KeePassXC-2.7.12-arm64.dmg")
        self.assertEqual(github.recommend([{"name": "Ollama.dmg"}, {"name": "ollama-darwin.tgz"}], "package", "arm64")["name"],
                         "Ollama.dmg")
        self.assertIsNone(github.recommend([{"name": "tool-windows-amd64.zip"}], "portable", "arm64"))

    def test_checksum_line(self):
        digest = "a" * 64
        body = digest + "  CLIProxyAPI_8.0.22_darwin_aarch64.tar.gz\n" + ("b" * 64) + "  other.zip\n"

        class Opener:
            def open(self, url, timeout=45):
                return io.BytesIO(body.encode())

        release = {"assets": [{"name": "checksums.txt", "url": "https://example.test/checksums.txt"}]}
        asset = {"name": "CLIProxyAPI_8.0.22_darwin_aarch64.tar.gz", "digest": ""}
        self.assertEqual(github.expected_sha256(release, asset, Opener()), digest)


class ArchiveTests(unittest.TestCase):
    def test_rejects_parent_path_and_extracts_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "pkg.tar.gz"
            with tarfile.open(archive, "w:gz") as package:
                info = tarfile.TarInfo("../evil")
                info.size = 4
                package.addfile(info, io.BytesIO(b"evil"))
            stage = root / "stage"
            with self.assertRaises(RuntimeError):
                extract_archive(archive, stage, archive.name)

    def test_unwraps_single_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "pkg.tar.gz"
            payload = b"#!/bin/sh\n"
            with tarfile.open(archive, "w:gz") as package:
                info = tarfile.TarInfo("CLIProxyAPI/cli-proxy-api")
                info.size = len(payload)
                info.mode = 0o755
                package.addfile(info, io.BytesIO(payload))
            content = extract_archive(archive, root / "stage", archive.name)
            binary = content / "cli-proxy-api"
            self.assertTrue(binary.is_file())
            if sys.platform != "win32":
                self.assertTrue(binary.stat().st_mode & 0o111)

    @unittest.skipUnless(os.name != "nt", "symlink")
    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "pkg.tar.gz"
            with tarfile.open(archive, "w:gz") as package:
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "cli-proxy-api"
                package.addfile(info)
            with self.assertRaises(RuntimeError):
                extract_archive(archive, root / "stage", archive.name)


class InstallTests(unittest.TestCase):
    def test_apply_payload_keeps_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            binary = stage / "cli-proxy-api"
            binary.write_text("new", encoding="utf-8")
            (stage / "config.yaml").write_text("host: 0.0.0.0\n", encoding="utf-8")
            (stage / "manager-admin-key.txt").write_text("untrusted-package-key", encoding="utf-8")
            target = root / "app"
            target.mkdir()
            (target / "config.yaml").write_text("host: 127.0.0.1\nport: 8317\n", encoding="utf-8")
            service.save_admin_key(target, "fixture-private-key")
            service.apply_payload(stage, target, PROJECTS["cli"], lambda *_args: None)
            self.assertEqual((target / "cli-proxy-api").read_text(encoding="utf-8"), "new")
            self.assertIn("127.0.0.1", (target / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual((target / "manager-admin-key.txt").read_text().strip(), "fixture-private-key")
            self.assertEqual((target / "manager-admin-key.txt").stat().st_mode & 0o777, 0o600)
            if sys.platform != "win32":
                self.assertTrue(os.access(target / "cli-proxy-api", os.X_OK))

    def test_refuses_source_tree(self):
        with self.assertRaises(ValueError):
            service.assert_install_directory(MAC_ROOT, MAC_ROOT)

    def test_get_mac_root_frozen(self):
        from unittest.mock import patch
        from cpa_mac.config import get_mac_root

        with patch.object(sys, "frozen", False, create=True):
            root = get_mac_root()
            self.assertEqual(root, MAC_ROOT)

        fake_exe = "/Applications/CPA Mac Manager.app/Contents/MacOS/CPA Mac Manager"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", fake_exe):
            root = get_mac_root()
            self.assertTrue(Path(root).as_posix().endswith("/Applications/CPA Mac Manager.app"))

        fake_binary = "/usr/local/bin/cpa-mac-manager"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", fake_binary):
            root = get_mac_root()
            self.assertTrue(Path(root).as_posix().endswith("/usr/local/bin"))

    def test_portable_preserve(self):
        with self.assertRaises(ValueError):
            software.parse_preserve("../secret")


class SettingsTests(unittest.TestCase):
    def test_roundtrip_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("CPA_MAC_HOME")
            os.environ["CPA_MAC_HOME"] = temporary
            try:
                settings_store.save_settings({"proxy_enabled": False, "proxy": "http://127.0.0.1:7890"})
                self.assertEqual(settings_store.load_settings()["proxy"], "http://127.0.0.1:7890")
                if sys.platform != "win32":
                    self.assertEqual(settings_store.settings_path().stat().st_mode & 0o777, 0o600)
            finally:
                if previous is None:
                    os.environ.pop("CPA_MAC_HOME", None)
                else:
                    os.environ["CPA_MAC_HOME"] = previous

    def test_zip_rejects_symlink_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "pkg.zip"
            with zipfile.ZipFile(archive, "w") as package:
                info = zipfile.ZipInfo("link")
                info.external_attr = (0o120777) << 16
                package.writestr(info, "cli-proxy-api")
            with self.assertRaises(RuntimeError):
                extract_archive(archive, root / "stage", archive.name)


class SearchTests(unittest.TestCase):
    def test_rejects_blank_and_overlong_queries(self):
        self.assertEqual(github.search_repositories("   "), ([], 0))
        with self.assertRaises(ValueError):
            github.search_repositories("a" * 257)

    def test_saved_asset_name_round_trip(self):
        from cpa_mac.state import load_data
        previous = os.environ.get("CPA_MAC_HOME")
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["CPA_MAC_HOME"] = temporary
            try:
                settings_store.save_settings({
                    "cli": {"directory": str(Path(temporary) / "cli"), "latest": "1.2.3", "asset": "cli.tar.gz"},
                    "portable": [{
                        "id": "a", "name": "fd", "repository": "https://github.com/sharkdp/fd",
                        "directory": str(Path(temporary) / "fd"), "latest": "v10.0.0", "asset": "fd.tar.gz",
                    }],
                })
                data = load_data()
                self.assertEqual(data["cli"]["asset"], "cli.tar.gz")
                self.assertEqual(data["portable"][0]["asset"], "fd.tar.gz")
            finally:
                if previous is None:
                    os.environ.pop("CPA_MAC_HOME", None)
                else:
                    os.environ["CPA_MAC_HOME"] = previous


class WebTests(unittest.TestCase):
    def test_page_lists_the_four_features(self):
        import json as json_lib
        import threading
        import urllib.request
        from unittest.mock import patch
        from cpa_mac import webapp
        from http.server import ThreadingHTTPServer

        previous = os.environ.get("CPA_MAC_HOME")
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["CPA_MAC_HOME"] = temporary
            webapp.STORE = None
            server = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                page = opener.open(url, timeout=15).read().decode()
                self.assertIn("CPA Mac 管理器", page)
                self.assertIn("CLIProxyAPI", page)
                self.assertIn("CPA-Manager-Plus", page)
                self.assertIn("免安装软件", page)
                self.assertIn("安装包", page)
                with patch("cpa_mac.webapp.project_state", return_value={}):
                    state = json_lib.loads(opener.open(url + "api/state", timeout=15).read().decode())
                self.assertEqual(set(state["projects"]), {"cli", "plus"})
                self.assertIn("fd", [item["name"] for item in state["catalog"]])
                self.assertIn("打开页面", page)
                self.assertIn("搜索 GitHub", page)
                self.assertIn("取消", page)
            finally:
                server.shutdown()
                webapp.STORE = None
                if previous is None:
                    os.environ.pop("CPA_MAC_HOME", None)
                else:
                    os.environ["CPA_MAC_HOME"] = previous


class OptimizationTests(unittest.TestCase):
    def test_normalize_download_workers(self):
        from cpa_mac.config import normalize_download_workers
        self.assertEqual(normalize_download_workers(20), 16)
        self.assertEqual(normalize_download_workers(0), 1)
        self.assertEqual(normalize_download_workers(-5), 1)
        self.assertEqual(normalize_download_workers(8), 8)
        self.assertEqual(normalize_download_workers("12"), 12)
        self.assertEqual(normalize_download_workers("invalid"), 4)

    def test_github_release_10min_cache(self):
        from unittest.mock import patch
        import json as json_lib
        github.clear_release_cache()
        repo = "https://github.com/owner/demo"
        release_json = {
            "tag_name": "v1.2.0",
            "prerelease": False,
            "assets": [{"name": "demo-darwin-arm64.tar.gz", "browser_download_url": "https://example.com/demo.tar.gz", "size": 1024}]
        }
        with patch("cpa_mac.backends.github.read_text", return_value=json_lib.dumps(release_json)) as read_mock:
            # First fetch calls network
            r1 = github.fetch_latest(repo, "")
            self.assertEqual(read_mock.call_count, 1)
            self.assertEqual(r1["tag"], "v1.2.0")

            # Second fetch hits 10min cache, no network
            r2 = github.fetch_latest(repo, "")
            self.assertEqual(read_mock.call_count, 1)
            self.assertEqual(r2["tag"], "v1.2.0")

            # Force fetch bypasses cache
            r3 = github.fetch_latest(repo, "", force=True)
            self.assertEqual(read_mock.call_count, 2)
            self.assertEqual(r3["tag"], "v1.2.0")

        github.clear_release_cache()

    def test_github_rate_limit_403_fallback_to_web_scraping(self):
        from unittest.mock import patch
        import urllib.error
        github.clear_release_cache()
        repo = "https://github.com/owner/demo"

        releases_html = '''
        <section>
            <a href="/owner/demo/releases/tag/v2.5.0">v2.5.0</a>
        </section>
        '''
        assets_html = '''
        <ul>
            <li class="Box-row">
                <a href="/owner/demo/releases/download/v2.5.0/demo-darwin-arm64.tar.gz">demo-darwin-arm64.tar.gz</a>
                <span>sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef</span>
                <span>8.5 MB</span>
            </li>
        </ul>
        '''

        def mock_read_text(opener, url):
            if "api.github.com" in url:
                raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)
            if url.endswith("/releases"):
                return releases_html
            if "expanded_assets" in url:
                return assets_html
            raise RuntimeError(f"Unexpected url: {url}")

        with patch("cpa_mac.backends.github.read_text", side_effect=mock_read_text):
            rel = github.fetch_latest(repo, "")
            self.assertEqual(rel["tag"], "v2.5.0")
            self.assertEqual(len(rel["assets"]), 1)
            self.assertEqual(rel["assets"][0]["name"], "demo-darwin-arm64.tar.gz")
            self.assertEqual(rel["assets"][0]["digest"], "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        github.clear_release_cache()

    def test_settings_api_workers_and_prerelease_roundtrip(self):
        import json as json_lib
        import threading
        import urllib.request
        from unittest.mock import patch
        from cpa_mac import webapp
        from http.server import ThreadingHTTPServer

        previous = os.environ.get("CPA_MAC_HOME")
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["CPA_MAC_HOME"] = temporary
            webapp.STORE = None
            server = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req_data = json_lib.dumps({
                    "proxy_enabled": True,
                    "proxy": "http://127.0.0.1:8888",
                    "download_directory": temporary,
                    "download_workers": 8,
                    "prerelease": True
                }).encode("utf-8")
                req = urllib.request.Request(url + "api/settings", data=req_data, headers={"Content-Type": "application/json"})
                with patch("cpa_mac.webapp.project_state", return_value={}):
                    resp = json_lib.loads(opener.open(req, timeout=15).read().decode())
                self.assertEqual(resp["download_workers"], 8)
                self.assertTrue(resp["prerelease"])
                self.assertTrue(resp["proxy_enabled"])
                self.assertEqual(resp["proxy"], "http://127.0.0.1:8888")

                # Verify persistence
                loaded = webapp.get_store().data
                self.assertEqual(loaded["download_workers"], 8)
                self.assertTrue(loaded["prerelease"])
            finally:
                server.shutdown()
                webapp.STORE = None
                if previous is None:
                    os.environ.pop("CPA_MAC_HOME", None)
                else:
                    os.environ["CPA_MAC_HOME"] = previous




class EnhancedFeaturesTests(unittest.TestCase):
    def test_admin_key_save_and_find(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # 1. 验证拒绝非法 key
            with self.assertRaises(ValueError):
                service.save_admin_key(root, "")
            with self.assertRaises(ValueError):
                service.save_admin_key(root, "invalid key with spaces")

            # 2. 验证正常保存与读取
            service.save_admin_key(root, "my_secret_token_123")
            key = service.find_admin_key(root)
            self.assertEqual(key, "my_secret_token_123")

    def test_admin_key_log_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "manager-service.log"
            log.write_text("2026/10/11 12:30:00 [info] starting server\n2026/10/11 12:30:01 CPA Manager Plus admin key generated: autogen_key_abc\n", encoding="utf-8")
            key = service.find_admin_key(root)
            self.assertEqual(key, "autogen_key_abc")

    def test_backups_clean_and_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(service.backup_count(root), 0)
            backup1 = root / ".update-backups" / "20261011-120000-abcdef12"
            backup2 = root / ".update-backups" / "20261011-120001-abcdef34"
            backup1.mkdir(parents=True)
            backup2.mkdir(parents=True)
            self.assertEqual(service.backup_count(root), 2)
            cleared = service.clear_backups(root)
            self.assertEqual(cleared, 2)
            self.assertEqual(service.backup_count(root), 0)

    def test_portable_uninstall_and_launch_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dummy_app = root / "my_app"
            dummy_app.mkdir()
            (dummy_app / "dummy.txt").write_text("hello")
            
            # 拒绝删除根目录或管理器目录
            with self.assertRaises(ValueError):
                software.uninstall_portable("/", "/some/manager")

            software.uninstall_portable(str(dummy_app), "/some/manager")
            self.assertFalse(dummy_app.exists())

    def test_delete_package_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pkg = folder / "test_app.dmg"
            pkg.write_text("data")
            self.assertTrue(pkg.is_file())
            self.assertTrue(software.delete_package_file(folder, "test_app.dmg"))
            self.assertFalse(pkg.exists())
            # 非法文件名保护
            self.assertFalse(software.delete_package_file(folder, "../etc/passwd"))

    def test_settings_export_and_import(self):
        from cpa_mac import state
        with tempfile.TemporaryDirectory() as temp:
            prev = os.environ.get("CPA_MAC_HOME")
            os.environ["CPA_MAC_HOME"] = temp
            try:
                exported = state.export_settings_json()
                import json
                data = json.loads(exported)
                self.assertIn("download_workers", data)
                self.assertIn("cli", data)
                self.assertIn("plus", data)
                data["download_workers"] = 12
                state.import_settings_json(json.dumps(data))
                reloaded = state.load_data()
                self.assertEqual(reloaded["download_workers"], 12)
            finally:
                if prev is None:
                    os.environ.pop("CPA_MAC_HOME", None)
                else:
                    os.environ["CPA_MAC_HOME"] = prev

    def test_catalog_categories_and_items(self):
        from cpa_mac.config import CATALOG, CATALOG_CATEGORIES
        self.assertIn("全部", CATALOG_CATEGORIES)
        self.assertIn("终端工具", CATALOG_CATEGORIES)
        self.assertIn("Git / 开发", CATALOG_CATEGORIES)
        self.assertTrue(len(CATALOG) >= 30)
        for item in CATALOG:
            self.assertIn(item[0], ("免安装", "安装包"))
            self.assertIn(item[1], CATALOG_CATEGORIES)




class MacOSNativeAdaptationTests(unittest.TestCase):
    def test_system_doctor(self):
        from cpa_mac.core.macos import system_doctor
        doc = system_doctor()
        self.assertEqual(doc["os"], "macOS")
        self.assertIn("architecture", doc)
        self.assertIn("free_disk_gb", doc)
        self.assertIn("8317", doc["ports"])
        self.assertIn("18317", doc["ports"])
        self.assertIn("cli", doc["launch_agents"])
        self.assertIn("plus", doc["launch_agents"])

    def test_port_listener_and_cleanup(self):
        from cpa_mac.core.macos import check_port_listener
        # 测试不存在的高位随机端口
        info = check_port_listener(59999)
        self.assertTrue(info is None or isinstance(info, dict))

    def test_remove_quarantine_safety(self):
        from cpa_mac.core.macos import remove_quarantine
        with tempfile.TemporaryDirectory() as temp:
            dummy = Path(temp) / "test_bin"
            dummy.write_text("binary content")
            self.assertTrue(remove_quarantine(dummy))
            self.assertFalse(remove_quarantine(Path(temp) / "non_existent"))

    def test_link_to_user_bin(self):
        from cpa_mac.core.macos import link_to_user_bin
        with tempfile.TemporaryDirectory() as temp:
            dummy = Path(temp) / "test_cli_tool"
            dummy.write_text("#!/bin/sh\necho 1")
            os.chmod(dummy, 0o755)
            # 临时 mock Path.home
            import cpa_mac.core.macos as macos_mod
            old_home = Path.home
            try:
                Path.home = lambda: Path(temp)
                link = link_to_user_bin(dummy, "my-tool")
                self.assertTrue(link.is_symlink() or link.is_file())
                self.assertEqual(link.read_text(), dummy.read_text())
            finally:
                Path.home = old_home

    def test_launch_agent_labels_and_status(self):
        from cpa_mac.backends.service import service_launch_agent_label, launch_agent_status
        cli_spec = {"key": "cli", "title": "CLIProxyAPI"}
        plus_spec = {"key": "plus", "title": "CPA-Manager-Plus"}
        self.assertEqual(service_launch_agent_label(cli_spec), "com.cpa.cliproxyapi")
        self.assertEqual(service_launch_agent_label(plus_spec), "com.cpa.cpamanagerplus")
        cli_status = launch_agent_status(cli_spec)
        self.assertIn("installed", cli_status)
        self.assertIn("loaded", cli_status)
        self.assertEqual(cli_status["label"], "com.cpa.cliproxyapi")



    def test_scan_installed_applications(self):
        from cpa_mac.core.macos import scan_installed_applications
        import plistlib
        with tempfile.TemporaryDirectory() as temp:
            app_dir = Path(temp) / "MockApp.app"
            contents = app_dir / "Contents"
            contents.mkdir(parents=True)
            plist_data = {
                "CFBundleDisplayName": "Mock App Display",
                "CFBundleName": "MockApp",
                "CFBundleShortVersionString": "2.4.1",
                "CFBundleIdentifier": "com.test.mockapp"
            }
            with open(contents / "Info.plist", "wb") as f:
                plistlib.dump(plist_data, f)

            apps = scan_installed_applications(custom_dirs=[temp])
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["name"], "Mock App Display")
            self.assertEqual(apps[0]["version"], "2.4.1")
            self.assertEqual(apps[0]["id"], "com.test.mockapp")
            self.assertEqual(apps[0]["directory"], str(app_dir))

    def test_five_hour_manager_update_throttling(self):
        import time
        from cpa_mac import webapp
        store = webapp.Store()
        store.data["manager_last_check"] = time.time() - 100 # Checked 100 seconds ago
        store.data["manager_release"] = {
            "current": "1.5.14",
            "latest": "1.5.14",
            "has_update": False,
            "url": "https://example.com"
        }
        handler = webapp.Handler.__new__(webapp.Handler)
        res = handler._post("/api/manager/check-update", {"force": False}, store)
        self.assertTrue(res.get("cached"))
        self.assertEqual(res["latest"], "1.5.14")

    def test_bind_and_unbind_installed_package(self):
        from cpa_mac import webapp
        store = webapp.Store()
        store.data["packages"] = [{
            "id": "test-pkg-1",
            "name": "LocalSend",
            "repository": "https://github.com/localsend/localsend",
            "directory": "",
            "preserve": "",
            "latest": "1.18.2",
            "asset": "localsend.dmg"
        }]
        handler = webapp.Handler.__new__(webapp.Handler)
        # Bind
        handler._post("/api/software/package/bind-installed", {
            "id": "test-pkg-1",
            "app_id": "com.localsend",
            "app_name": "LocalSend",
            "app_version": "1.18.0",
            "app_dir": "/Applications/LocalSend.app"
        }, store)
        rec = store.data["packages"][0]
        self.assertEqual(rec["installed_version"], "1.18.0")
        state = webapp.software_state(rec, "package")
        self.assertEqual(state["local"], "1.18.0")
        self.assertTrue(state["update"]) # 1.18.0 < 1.18.2 -> update should be true

        # Unbind
        handler._post("/api/software/package/unbind-installed", {"id": "test-pkg-1"}, store)
        self.assertEqual(store.data["packages"][0]["installed_version"], "")

    def test_package_and_portable_repository_edit(self):
        from cpa_mac import webapp
        store = webapp.Store()
        store.data["packages"] = [{
            "id": "pkg-edit-1",
            "name": "LocalSend",
            "repository": "https://github.com/localsend/localsend",
            "directory": "",
            "preserve": "",
            "latest": "1.18.2",
            "asset": "localsend.dmg",
            "selected_tag": "1.18.2",
            "selected_asset": "localsend.dmg"
        }, {
            "id": "pkg-edit-2",
            "name": "v2rayN",
            "repository": "https://github.com/2dust/v2rayN",
            "directory": "",
            "preserve": "",
            "latest": "",
            "asset": ""
        }]
        handler = webapp.Handler.__new__(webapp.Handler)

        # Edit package repository to a new repository
        handler._post("/api/software/package/edit", {
            "id": "pkg-edit-1",
            "repository": "https://github.com/clash-verge-rev/clash-verge-rev"
        }, store)
        pkg = store.data["packages"][0]
        self.assertEqual(pkg["repository"], "https://github.com/clash-verge-rev/clash-verge-rev")
        self.assertEqual(pkg["name"], "clash-verge-rev")
        self.assertEqual(pkg["latest"], "")
        self.assertEqual(pkg["asset"], "")
        self.assertEqual(pkg["selected_tag"], "")
        self.assertEqual(pkg["selected_asset"], "")

        # Duplicate repository is rejected
        with self.assertRaises(ValueError):
            handler._post("/api/software/package/edit", {
                "id": "pkg-edit-1",
                "repository": "https://github.com/2dust/v2rayN"
            }, store)

        # Portable repository edit
        store.data["portable"] = [{
            "id": "port-1",
            "name": "ripgrep",
            "repository": "https://github.com/BurntSushi/ripgrep",
            "directory": "/tmp/rg",
            "preserve": "config",
            "latest": "14.1.0",
            "asset": "rg.tar.gz"
        }]
        handler._post("/api/software/portable/edit", {
            "id": "port-1",
            "repository": "https://github.com/sharkdp/fd"
        }, store)
        port = store.data["portable"][0]
        self.assertEqual(port["repository"], "https://github.com/sharkdp/fd")
        self.assertEqual(port["name"], "fd")
        self.assertEqual(port["latest"], "")


    def test_cliproxyapi_port_detection_and_conflict(self):
        from unittest.mock import patch, MagicMock
        from cpa_mac.backends import service

        with patch.object(service, "is_cliproxyapi_installed", return_value=True), \
             patch.object(service, "get_cliproxyapi_port", return_value=8317), \
             patch.object(service, "get_expected_cliproxyapi_executables", return_value={"/opt/homebrew/bin/cliproxyapi"}), \
             patch.object(service, "find_brew_executable", return_value="/opt/homebrew/bin/brew"):

            # 1. 端口被其他地方程序占用
            fake_listener = {"port": 8317, "pid": 58043, "command": "cli-proxy"}
            fake_loc = {
                "pid": 58043,
                "name": "cli-proxy-api",
                "exe": "/Users/test/Library/cli-proxy-api",
                "cmdline": "/Users/test/Library/cli-proxy-api -config /test.yaml",
                "display": "/Users/test/Library/cli-proxy-api",
            }
            with patch.object(service, "check_port_listener", return_value=fake_listener), \
                 patch.object(service, "is_matching_cliproxyapi_process", return_value=False), \
                 patch.object(service, "get_process_location_info", return_value=fake_loc):
                status, is_running = service.cliproxyapi_service_status()
                self.assertFalse(is_running)
                self.assertIn("端口 8317 被其他程序占用", status)
                self.assertIn("58043", status)
                self.assertIn("/Users/test/Library/cli-proxy-api", status)

                conflict = service.cliproxyapi_port_conflict()
                self.assertIsNotNone(conflict)
                self.assertEqual(conflict["port"], 8317)
                self.assertEqual(conflict["pid"], 58043)
                self.assertEqual(conflict["path"], "/Users/test/Library/cli-proxy-api")

                # 尝试启动或重启必须报错并提示占用位置
                reports = []
                with self.assertRaisesRegex(RuntimeError, "端口 8317 已被其他程序占用.*PID 58043.*无法启动"):
                    service.control_cliproxyapi_brew("start", lambda p, l: reports.append(l))

                with self.assertRaisesRegex(RuntimeError, "端口 8317 已被其他程序占用.*PID 58043.*无法重启"):
                    service.control_cliproxyapi_brew("restart", lambda p, l: reports.append(l))

            # 2. 端口由官方 Homebrew 进程正常监听
            brew_listener = {"port": 8317, "pid": 12345, "command": "cliproxyapi"}
            with patch.object(service, "check_port_listener", return_value=brew_listener), \
                 patch.object(service, "is_matching_cliproxyapi_process", return_value=True):
                status, is_running = service.cliproxyapi_service_status()
                self.assertTrue(is_running)
                self.assertEqual(status, "运行中（PID 12345）")
                self.assertIsNone(service.cliproxyapi_port_conflict())

                reports = []
                service.control_cliproxyapi_brew("start", lambda p, l: reports.append(l))
                self.assertTrue(any("已在运行中" in r for r in reports))

            # 3. 端口空闲时正常启动
            with patch.object(service, "check_port_listener", return_value=None), \
                 patch.object(service.subprocess, "run", return_value=MagicMock(returncode=0)) as sub_run:
                status, is_running = service.cliproxyapi_service_status()
                self.assertFalse(is_running)
                self.assertEqual(status, "未运行")

                reports = []
                service.control_cliproxyapi_brew("start", lambda p, l: reports.append(l))
                self.assertTrue(any("已启动" in r for r in reports))
                sub_run.assert_called()



    def test_stopped_cliproxyapi_only_inspects_candidate_processes(self):
        from unittest.mock import MagicMock, patch
        unrelated = [MagicMock(pid=identity, info={"name": "unrelated"}) for identity in range(500)]
        candidate = MagicMock(pid=1000, info={"name": "cliproxyapi-8.0.25"})
        with patch.object(service, "is_cliproxyapi_installed", return_value=True), \
             patch.object(service, "get_cliproxyapi_port", return_value=8317), \
             patch.object(service, "check_port_listener", return_value=None), \
             patch.object(service, "get_expected_cliproxyapi_executables", return_value={"/brew/cliproxyapi"}), \
             patch.object(service.psutil, "process_iter", return_value=unrelated + [candidate]), \
             patch.object(service, "is_matching_cliproxyapi_process", return_value=False) as matches:
            self.assertEqual(service.cliproxyapi_service_status(), ("未运行", False))
            matches.assert_called_once_with(1000, {"/brew/cliproxyapi"})
            matches.return_value = True
            self.assertEqual(service.cliproxyapi_service_status(), ("运行中（PID 1000）", True))

    def test_process_executable_mismatch_does_not_run_ps_or_trust_argv(self):
        from unittest.mock import MagicMock, patch
        process = MagicMock()
        process.exe.return_value = "/other/cliproxyapi"
        process.cmdline.return_value = ["/brew/cliproxyapi"]
        with patch.object(service, "get_brew_prefix", return_value="/brew"), \
             patch.object(service.psutil, "Process", return_value=process), \
             patch.object(service.subprocess, "run") as run:
            self.assertFalse(service.is_matching_cliproxyapi_process(123, {"/brew/cliproxyapi"}))
            run.assert_not_called()
            process.cmdline.assert_not_called()

    def test_cliproxyapi_recognizes_cellar_versioned_binary(self):
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            cellar_bin = tmppath / "Cellar" / "cliproxyapi" / "8.0.20" / "bin"
            cellar_bin.mkdir(parents=True)
            versioned_exe = cellar_bin / "cliproxyapi-8.0.25"
            versioned_exe.write_text("#!/bin/sh\n", encoding="utf-8")
            versioned_exe.chmod(0o755)

            wrapper = cellar_bin / "cliproxyapi"
            wrapper.write_text(f"#!/bin/sh\nexec \"{versioned_exe}\" \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)

            with patch.object(service, "get_brew_prefix", return_value=str(tmppath)), \
                 patch.object(service.shutil, "which", return_value=str(wrapper)):
                expected = service.get_expected_cliproxyapi_executables()
                self.assertIn(str(versioned_exe), expected)
                self.assertIn(str(wrapper), expected)

                # 模拟正在运行该版本二进制的进程
                mock_proc = MagicMock()
                mock_proc.exe.return_value = str(versioned_exe)
                mock_proc.cmdline.return_value = [str(versioned_exe), "-config", "/path/to/conf"]
                with patch.object(service.psutil, "Process", return_value=mock_proc):
                    self.assertTrue(service.is_matching_cliproxyapi_process(3833, expected))

                # 模拟第三方外部进程
                third_party_proc = MagicMock()
                third_party_proc.exe.return_value = "/Users/test/Library/cli-proxy-api"
                third_party_proc.cmdline.return_value = ["/Users/test/Library/cli-proxy-api"]
                with patch.object(service.psutil, "Process", return_value=third_party_proc):
                    self.assertFalse(service.is_matching_cliproxyapi_process(84244, expected))

if __name__ == "__main__":
    unittest.main()

