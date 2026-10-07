import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cpa_manager.backends import cli as cli_backend
from cpa_manager.backends import plus as plus_backend
from cpa_manager.backends import manager_update
from cpa_manager.backends import github
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.runtime import accepts_prerelease
from cpa_manager.app import App, read_settings


class PrereleaseTests(unittest.TestCase):
    def test_accepts_prerelease_helper(self):
        def f1(a, b):
            pass
        def f2(a, b, include_prerelease=False):
            pass
        def f3(*args, **kwargs):
            pass

        self.assertFalse(accepts_prerelease(f1))
        self.assertTrue(accepts_prerelease(f2))
        self.assertTrue(accepts_prerelease(f3))
        self.assertFalse(accepts_prerelease(None))

    def test_manager_update_prerelease_selection(self):
        prefix_beta = "/Xunzi229/CPA-Win-Manager/releases/download/v1.6.0-beta.1/"
        file_beta = "CPA-Unified-Manager-v1.6.0-beta.1-windows-amd64.zip"
        assets_beta = f'<a href="{prefix_beta}{file_beta}"><a href="{prefix_beta}SHA256SUMS.txt">'

        prefix_stable = "/Xunzi229/CPA-Win-Manager/releases/download/v1.5.11/"
        file_stable = "CPA-Unified-Manager-v1.5.11-windows-amd64.zip"
        assets_stable = f'<a href="{prefix_stable}{file_stable}"><a href="{prefix_stable}SHA256SUMS.txt">'

        releases_page = """
        <a href="/Xunzi229/CPA-Win-Manager/releases/expanded_assets/v1.6.0-beta.1">
        <a href="/Xunzi229/CPA-Win-Manager/releases/expanded_assets/v1.5.11">
        """
        latest_page = """
        <a href="/Xunzi229/CPA-Win-Manager/releases/expanded_assets/v1.5.11">
        """

        # When include_prerelease is True, pulls v1.6.0-beta.1
        with patch.object(manager_update, "windows_architecture", return_value="amd64"), \
             patch.object(cli_backend, "read_text", side_effect=(releases_page, assets_beta)):
            release = manager_update.latest_release("", include_prerelease=True)
            self.assertEqual(release[0], "v1.6.0-beta.1")

        # When include_prerelease is False, pulls v1.5.11
        with patch.object(manager_update, "windows_architecture", return_value="amd64"), \
             patch.object(cli_backend, "read_text", side_effect=(latest_page, assets_stable)):
            release = manager_update.latest_release("", include_prerelease=False)
            self.assertEqual(release[0], "v1.5.11")

    def test_cli_backend_prerelease_selection(self):
        prefix_beta = "/router-for-me/CLIProxyAPI/releases/download/v8.1.0-rc.1/"
        file_beta = "/router-for-me/CLIProxyAPI/releases/download/v8.1.0-rc.1/CLIProxyAPI_8.1.0-rc.1_windows_amd64.zip"
        assets_beta = f'<a href="{file_beta}"><a href="{prefix_beta}checksums.txt">'

        releases_page = """
        <a href="/router-for-me/CLIProxyAPI/releases/expanded_assets/v8.1.0-rc.1">
        <a href="/router-for-me/CLIProxyAPI/releases/expanded_assets/v8.0.12">
        """
        with patch.object(cli_backend, "windows_architecture", return_value="amd64"), \
             patch.object(cli_backend, "read_text", side_effect=(releases_page, assets_beta)):
            tag, pkg, _ = cli_backend.latest_release(None, include_prerelease=True)
            self.assertEqual(tag, "v8.1.0-rc.1")

    def test_plus_backend_prerelease_selection(self):
        prefix_beta = "/seakee/CPA-Manager-Plus/releases/download/v1.15.0-alpha.1/"
        file_beta = "/seakee/CPA-Manager-Plus/releases/download/v1.15.0-alpha.1/cpa-manager-plus_v1.15.0-alpha.1_windows_amd64.zip"
        assets_beta = f'<a href="{file_beta}"><a href="{prefix_beta}checksums.txt">'

        releases_page = """
        <a href="/seakee/CPA-Manager-Plus/releases/expanded_assets/v1.15.0-alpha.1">
        <a href="/seakee/CPA-Manager-Plus/releases/expanded_assets/v1.14.4">
        """
        with patch.object(plus_backend, "windows_architecture", return_value="amd64"), \
             patch.object(plus_backend, "read_text", side_effect=(releases_page, assets_beta)):
            tag, pkg, _ = plus_backend.latest_release(None, include_prerelease=True)
            self.assertEqual(tag, "v1.15.0-alpha.1")

    def test_github_release_catalog_prerelease(self):
        repo = "https://github.com/owner/demo"
        stable = {
            "tag_name": "v1.0.0",
            "prerelease": False,
            "assets": [{
                "name": "demo.zip",
                "browser_download_url": "https://github.com/owner/demo/releases/download/v1.0.0/demo.zip",
                "size": 100
            }]
        }
        beta = {
            "tag_name": "v2.0.0-beta.1",
            "prerelease": True,
            "assets": [{
                "name": "demo.zip",
                "browser_download_url": "https://github.com/owner/demo/releases/download/v2.0.0-beta.1/demo.zip",
                "size": 100
            }]
        }
        listing = [beta, stable]

        # include_prerelease=False: first element is stable
        with patch("cpa_manager.backends.github.network"), \
             patch("cpa_manager.backends.github.read_text", side_effect=[json.dumps(stable), json.dumps(listing)]):
            cat_stable = github.release_catalog(repo, "", include_prerelease=False)
            self.assertEqual(cat_stable[0]["tag"], "v1.0.0")

        # include_prerelease=True: first element is pre-release
        with patch("cpa_manager.backends.github.network"), \
             patch("cpa_manager.backends.github.read_text", side_effect=[json.dumps(stable), json.dumps(listing)]):
            cat_beta = github.release_catalog(repo, "", include_prerelease=True)
            self.assertEqual(cat_beta[0]["tag"], "v2.0.0-beta.1")

    def test_github_release_catalog_only_prerelease(self):
        repo = "https://github.com/owner/prerelease-only"
        beta = {
            "tag_name": "v0.1.0-alpha.1",
            "prerelease": True,
            "assets": [{
                "name": "app.zip",
                "browser_download_url": "https://github.com/owner/prerelease-only/releases/download/v0.1.0-alpha.1/app.zip",
                "size": 200
            }]
        }
        listing = [beta]

        # When include_prerelease=True, works even if /releases/latest raises RuntimeError (no stable release)
        with patch("cpa_manager.backends.github.network"), \
             patch("cpa_manager.backends.github.releases", side_effect=RuntimeError("No stable release")), \
             patch("cpa_manager.backends.github.read_text", return_value=json.dumps(listing)):
            cat = github.release_catalog(repo, "", include_prerelease=True)
            self.assertEqual(len(cat), 1)
            self.assertEqual(cat[0]["tag"], "v0.1.0-alpha.1")

    def test_release_cache_prerelease_separation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = ReleaseCache(tmp)
            repo = "https://github.com/test/repo"
            cat_stable = [{"repository": repo, "tag": "v1.0.0", "notes": "", "assets": []}]
            cat_prerelease = [{"repository": repo, "tag": "v1.1.0-rc.1", "notes": "", "assets": []}]

            self.assertTrue(cache.put(repo, cat_stable, include_prerelease=False))
            self.assertTrue(cache.put(repo, cat_prerelease, include_prerelease=True))
            cache.flush()

            # Cache key separation verified
            loaded_cache = ReleaseCache(tmp)
            self.assertEqual(loaded_cache.get(repo, include_prerelease=False), cat_stable)
            self.assertEqual(loaded_cache.get(repo, include_prerelease=True), cat_prerelease)

            # Clear cache
            loaded_cache.clear()
            self.assertEqual(loaded_cache.get(repo, include_prerelease=False), [])
            self.assertEqual(loaded_cache.get(repo, include_prerelease=True), [])

    def test_app_settings_prerelease_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "manager-profiles.json"
            app = App.__new__(App)
            app.settings_file = config_file
            app.profiles = {}
            app.custom_profiles = []
            app.installer_profiles = []
            app.proxy_settings = {"enabled": False, "url": ""}
            app.include_prerelease = False
            app.installer_download_directory = tmp
            app.download_workers = 4
            app.release_cache = ReleaseCache(tmp)
            app.manager_release = ("v1.0.0", "url", "sha")
            app.manager_last_check = 123456
            app.proxy_status = Mock()
            app.pages = []

            # Save initial
            app.save()
            data = read_settings(config_file)
            self.assertFalse(data.get("include_prerelease", False))

            # Enable prerelease
            app.set_settings(False, "", tmp, 4, include_prerelease=True)
            self.assertTrue(app.include_prerelease)
            self.assertIsNone(app.manager_release)
            self.assertIsNone(app.manager_last_check)
            data = read_settings(config_file)
            self.assertTrue(data.get("include_prerelease"))


    def test_settings_dialog_separate_prerelease_and_manager_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "manager-profiles.json"
            app = App.__new__(App)
            app.settings_file = config_file
            app.profiles = {}
            app.custom_profiles = []
            app.installer_profiles = []
            app.proxy_settings = {"enabled": False, "url": ""}
            app.include_prerelease = False
            app.installer_download_directory = tmp
            app.download_workers = 4
            app.release_cache = ReleaseCache(tmp)
            app.proxy_dialog = None
            app.manager_version = "1.5.11"
            app.manager_status = Mock()
            app.manager_busy = False
            app.refresh_manager_controls = Mock()

            import tkinter as tk
            from tkinter import ttk
            root = tk.Tk()
            app.window = root

            app.open_proxy_settings()
            dialog = app.proxy_dialog
            self.assertIsNotNone(dialog)

            # Find all ttk.LabelFrame in dialog
            labelframes = []
            def find_labelframes(w):
                if isinstance(w, ttk.LabelFrame):
                    labelframes.append(w.cget("text"))
                for child in w.winfo_children():
                    find_labelframes(child)
            find_labelframes(dialog)

            self.assertIn("预发版本设置", labelframes)
            self.assertIn("管理器更新", labelframes)
            dialog.destroy()
            root.destroy()

    def test_portable_page_update_badge_red_dot(self):
        import tkinter as tk
        from tkinter import ttk
        from cpa_manager.ui.pages.portable import PortablePage
        from cpa_manager.backends.release_cache import ReleaseCache

        root = tk.Tk()
        notebook = ttk.Notebook(root)
        from types import SimpleNamespace
        app = SimpleNamespace(
            window=root,
            include_prerelease=False,
            manager_busy=False,
            table_height_portable=5,
            installer_download_directory="",
            release_cache=ReleaseCache(),
            save=Mock(),
            custom_profiles=[
                {"id": "p1", "name": "v2rayN", "directory": "D:/Tools/test", "repository": "https://github.com/2dust/v2rayN",
                 "selected_version": "7.25.5", "selected_asset": ""},
                {"id": "p2", "name": "FIClash", "directory": "D:/Tools/test", "repository": "https://github.com/chen08209/FIClash",
                 "selected_version": "v0.8.99", "selected_asset": ""},
                {"id": "p3", "name": "cc-switch", "directory": "D:/Tools/test", "repository": "https://github.com/farion1231/cc-switch",
                 "selected_version": "v4.0.3", "selected_asset": ""},
            ]
        )

        with patch("cpa_manager.backends.portable.local_version", side_effect=lambda d, r: "7.25.4" if "v2rayN" in r else ("v0.8.99" if "FIClash" in r else "v3.20.4")):
            page = PortablePage(app, notebook, Path("."))
            page.row_catalogs = {
                "p1": [{"tag": "7.25.5", "assets": []}],
                "p2": [{"tag": "v0.8.99", "assets": []}],
                "p3": [{"tag": "v4.0.3", "assets": []}],
            }
            page.update_labels()

            # p1 (7.25.4 < 7.25.5) and p3 (v3.20.4 < v4.0.3) should have red dot badge
            self.assertIn("p1", page.update_badges.rows)
            self.assertIn("p3", page.update_badges.rows)
            # p2 (v0.8.99 == v0.8.99) should NOT have badge
            self.assertNotIn("p2", page.update_badges.rows)

        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        notebook.destroy()
        root.destroy()



    def test_github_release_catalog_10min_cache(self):
        import time
        from urllib.error import HTTPError
        github.clear_catalog_cache()
        repo = "https://github.com/owner/demo"
        release = {
            "tag_name": "v1.0.0",
            "prerelease": False,
            "assets": [{
                "name": "demo.zip",
                "browser_download_url": "https://github.com/owner/demo/releases/download/v1.0.0/demo.zip",
                "size": 1024
            }]
        }
        with patch.object(github, "network"), \
             patch.object(github, "read_text", side_effect=[json.dumps(release), json.dumps([release]), json.dumps(release), json.dumps([release])]) as read_mock:
            # First call fetches via network
            c1 = github.release_catalog(repo, "", max_age=600)
            self.assertEqual(read_mock.call_count, 2)
            self.assertEqual(c1[0]["tag"], "v1.0.0")

            # Second call within 10 min uses cache, read_text not called again
            c2 = github.release_catalog(repo, "", max_age=600)
            self.assertEqual(read_mock.call_count, 2)
            self.assertEqual(c2[0]["tag"], "v1.0.0")

            # Force bypasses cache
            c3 = github.release_catalog(repo, "", max_age=600, force=True)
            self.assertEqual(read_mock.call_count, 4)

        github.clear_catalog_cache()

    def test_github_rate_limit_403_fallback_to_web_scraping(self):
        import urllib.error
        github.clear_catalog_cache()
        repo = "https://github.com/owner/demo"

        # HTML mock for /releases and /expanded_assets/v2.0.0
        releases_html = '''
        <section aria-labelledby="r1">
            <a href="/owner/demo/releases/tag/v2.0.0">v2.0.0</a>
            <div data-test-selector="body-content">版本更新说明</div>
        </section>
        '''
        assets_html = '''
        <ul>
            <li class="Box-row">
                <a href="/owner/demo/releases/download/v2.0.0/demo-windows-amd64.zip">
                    <span class="text-bold">demo-windows-amd64.zip</span>
                </a>
                <span>sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef</span>
                <span class="color-fg-muted">10.5 MB</span>
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
            raise RuntimeError(f"Unexpected URL: {url}")

        with patch.object(github, "network"), \
             patch.object(github, "read_text", side_effect=mock_read_text):
            catalog = github.release_catalog(repo, "", max_age=600)
            self.assertEqual(len(catalog), 1)
            self.assertEqual(catalog[0]["tag"], "v2.0.0")
            self.assertEqual(catalog[0]["assets"][0]["name"], "demo-windows-amd64.zip")
            self.assertEqual(catalog[0]["assets"][0]["size"], int(10.5 * 1024 * 1024))
            self.assertEqual(catalog[0]["assets"][0]["digest"], "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        github.clear_catalog_cache()

if __name__ == "__main__":
    unittest.main()

