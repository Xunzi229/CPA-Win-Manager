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
            target = root / "app"
            target.mkdir()
            (target / "config.yaml").write_text("host: 127.0.0.1\nport: 8317\n", encoding="utf-8")
            service.apply_payload(stage, target, PROJECTS["cli"], lambda *_args: None)
            self.assertEqual((target / "cli-proxy-api").read_text(encoding="utf-8"), "new")
            self.assertIn("127.0.0.1", (target / "config.yaml").read_text(encoding="utf-8"))
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
                page = urllib.request.urlopen(url, timeout=5).read().decode()
                self.assertIn("CPA Mac 管理器", page)
                self.assertIn("CLIProxyAPI", page)
                self.assertIn("CPA-Manager-Plus", page)
                self.assertIn("免安装软件", page)
                self.assertIn("安装包", page)
                state = json_lib.loads(urllib.request.urlopen(url + "api/state", timeout=5).read().decode())
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
                req_data = json_lib.dumps({
                    "proxy_enabled": True,
                    "proxy": "http://127.0.0.1:8888",
                    "download_directory": temporary,
                    "download_workers": 8,
                    "prerelease": True
                }).encode("utf-8")
                req = urllib.request.Request(url + "api/settings", data=req_data, headers={"Content-Type": "application/json"})
                resp = json_lib.loads(urllib.request.urlopen(req, timeout=5).read().decode())
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


if __name__ == "__main__":
    unittest.main()
