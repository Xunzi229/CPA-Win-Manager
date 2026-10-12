"""Mac release selection tests without real installations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpa_mac import native_worker as worker
from cpa_mac.backends import github, release_selection as selection, software


class ReleaseSelectionTests(unittest.TestCase):
    def release(self, tag="v2.0.0"):
        return {"tag": tag, "assets": [
            {"name": "tool-macos-arm64.dmg", "url": "https://example.test/tool.dmg"},
            {"name": "tool-macos-arm64.zip", "url": "https://example.test/tool.zip"},
            {"name": "tool-macos-x64.dmg", "url": "https://example.test/intel.dmg"},
            {"name": "tool-windows-arm64.zip", "url": "https://example.test/win.zip"},
            {"name": "tool-linux-arm64.zip", "url": "https://example.test/linux.zip"},
            {"name": "tool-macos-arm64.dmg.sha256", "url": "https://example.test/checksum"}]}

    def request(self, root, action):
        return {"scope": "software", "mode": "package", "action": action, "source_root": str(root / "manager"),
                "body": {"id": "test", "repository": "example/test"}, "config": {
                    "proxy_enabled": False, "proxy": "", "prerelease": False, "download_workers": 4,
                    "download_directory": str(root / "downloads"), "portable": [], "packages": [{
                        "id": "test", "name": "test", "repository": "example/test", "directory": str(root / "tool"),
                        "preserve": "", "latest": "v3.0.0", "local": "1.0.0", "selected_tag": "v2.0.0",
                        "selected_asset": "tool-macos-arm64.zip"}]}}

    def test_paginated_api_filters_draft_and_prerelease(self):
        data = [{"tag_name": "v1"}, {"tag_name": "v2", "prerelease": True}, {"tag_name": "draft", "draft": True}]
        with patch.object(github, "read_text", return_value=json.dumps(data)) as read:
            result = selection.list_releases("example/test", page=2)
            self.assertEqual(result["releases"], [{"tag": "v1", "prerelease": False}])
            self.assertIn("per_page=30&page=2", read.call_args.args[1])
            self.assertFalse(result["has_more"])
            self.assertEqual(len(selection.list_releases("example/test", include_prerelease=True)["releases"]), 2)
        with patch.object(github, "read_text", return_value=json.dumps([{"tag_name": str(index)} for index in range(30)])):
            self.assertTrue(selection.list_releases("example/test")["has_more"])
        for page in [0, 101, True, "2"]:
            with self.assertRaises(ValueError):
                selection.list_releases("example/test", page=page)

    def test_rate_limit_html_uses_proxy_and_supports_encoded_tags(self):
        error = urllib.error.HTTPError("https://api.github.com", 429, "limit", {}, None)
        html = '<section><a href="/example/test/releases/tag/release%2Fv2">v2</a></section><a rel="next">Next</a>'
        with patch.object(github, "read_text", side_effect=[error, html]), patch.object(github, "network") as network:
            result = selection.list_releases("example/test", "http://localhost:11080")
            self.assertEqual(result["releases"][0]["tag"], "release/v2")
            self.assertTrue(result["has_more"])
            network.assert_any_call("http://localhost:11080", accept="text/html")
        html = '<section><a href="/example/test/releases/tag/v3">v3</a>Pre-release</section><a rel=\'next\'>Next</a>'
        with patch.object(github, "read_text", side_effect=[error, html]):
            result = selection.list_releases("example/test")
            self.assertEqual(result["releases"], [])
            self.assertTrue(result["has_more"])

    def test_tag_endpoint_encodes_slashes_and_never_falls_back_to_latest(self):
        with patch.object(github, "read_text", return_value='{"tag_name":"release/v2","assets":[]}') as read:
            self.assertEqual(selection.fetch_release("example/test", "release/v2")["tag"], "release/v2")
            self.assertTrue(read.call_args.args[1].endswith("/tags/release%2Fv2"))
        with patch.object(github, "read_text", side_effect=OSError("offline")), patch.object(github, "fetch_latest") as latest:
            with self.assertRaisesRegex(RuntimeError, "不会自动"):
                selection.fetch_release("example/test", "v2")
            latest.assert_not_called()
        for tag in [0, None, False, "bad\nversion"]:
            with self.assertRaises(ValueError):
                selection.fetch_release("example/test", tag)

    def test_rate_limited_tag_scrapes_only_selected_release(self):
        error = urllib.error.HTTPError("https://api.github.com", 429, "limit", {}, None)
        with patch.object(github, "read_text", side_effect=error), \
             patch.object(github, "scrape_expanded_assets", return_value=self.release()["assets"]) as scrape:
            result = selection.fetch_release("example/test", "v2.0.0")
            self.assertEqual(result["tag"], "v2.0.0")
            self.assertEqual(scrape.call_args.args[1], "v2.0.0")

    def test_candidates_are_mac_native_and_include_manual_zip(self):
        names = [asset["name"] for asset in selection.compatible_assets(self.release(), "package", "arm64")]
        self.assertEqual(names, ["tool-macos-arm64.dmg", "tool-macos-arm64.zip"])
        record = {"repository": "example/test", "selected_tag": "v2.0.0", "selected_asset": names[1]}
        with patch.object(selection, "fetch_release", return_value=self.release()):
            self.assertEqual(selection.resolve(record, "package", "arm64")[1]["name"], names[1])
            for name in ["deleted.dmg", "tool-macos-x64.dmg", "tool-windows-arm64.zip"]:
                with self.assertRaisesRegex(ValueError, "不会静默"):
                    selection.resolve({**record, "selected_asset": name}, "package", "arm64")

    def test_v2rayn_numeric_intel_suffix_is_not_offered_on_apple_silicon(self):
        release = {"assets": [{"name": "v2rayN-macos-64.dmg"}, {"name": "v2rayN-macos-64.zip"},
                              {"name": "v2rayN-macos-arm64.dmg"}, {"name": "v2rayN-macos-arm64.zip"}]}
        assets = selection.compatible_assets(release, "package", "arm64")
        self.assertEqual([asset["name"] for asset in assets], ["v2rayN-macos-arm64.dmg", "v2rayN-macos-arm64.zip"])
        self.assertEqual(len(selection.compatible_assets(release, "package", "amd64")), 2)

    def test_manual_asset_pins_tag_and_auto_resets_preferences(self):
        record = {"repository": "example/test"}
        with patch.object(selection, "fetch_release", return_value=self.release()):
            result = selection.selection(record, "package", "arm64", {"tag": "", "asset": "tool-macos-arm64.zip"})
            self.assertEqual(result["selected_tag"], "v2.0.0")
            self.assertEqual(result["selected_asset"], "tool-macos-arm64.zip")
            result = selection.selection(record, "package", "arm64", {"tag": "", "asset": ""})
            self.assertEqual(result["selected_tag"], "")
            self.assertEqual(result["selected_asset"], "")

    def test_native_save_emits_persistent_selection_and_rejects_changed_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.request(Path(directory), "select_release")
            request["body"].update(tag="v2.0.0", asset="tool-macos-arm64.zip")
            events = []
            with patch.object(selection, "fetch_release", return_value=self.release()), patch.object(worker, "host_architecture", return_value="arm64"):
                worker.execute(request, events.append)
                self.assertEqual(events[0]["value"]["selected_tag"], "v2.0.0")
                self.assertEqual(events[0]["value"]["selected_asset"], "tool-macos-arm64.zip")
                request["body"]["repository"] = "different/repo"
                with self.assertRaisesRegex(ValueError, "仓库已修改"):
                    worker.execute(request, events.append)

    def test_pinned_install_uses_selected_zip_without_fetching_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.request(Path(directory), "install")
            with patch.object(selection, "fetch_release", return_value=self.release()) as fetch, \
                 patch.object(github, "fetch_latest") as latest, patch.object(software, "install_package") as install, \
                 patch.object(worker, "host_architecture", return_value="arm64"):
                worker.execute(request, lambda event: None)
                self.assertEqual(fetch.call_args.args[1], "v2.0.0")
                latest.assert_not_called()
                self.assertEqual(install.call_args.args[0]["tag"], "v2.0.0")
                self.assertEqual(install.call_args.args[1]["name"], "tool-macos-arm64.zip")

    def test_check_switches_to_latest_and_clears_manual_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.request(Path(directory), "check")
            events = []
            with patch.object(selection, "fetch_release", return_value=self.release()), \
                 patch.object(github, "fetch_latest", return_value=self.release("v3.0.0")), \
                 patch.object(worker, "host_architecture", return_value="arm64"):
                worker.execute(request, events.append)
            record = next(event["value"] for event in events if event["type"] == "record")
            self.assertEqual(record["latest"], "v3.0.0")
            self.assertEqual(record["asset"], "tool-macos-arm64.dmg")
            self.assertEqual(record["selected_tag"], "")
            self.assertEqual(record["selected_asset"], "")

    def test_bulk_respects_pinned_release_and_skips_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.request(Path(directory), "upgrade_all")
            with patch.object(selection, "fetch_release", return_value=self.release()), \
                 patch.object(software, "install_package") as install, \
                 patch.object(worker, "host_architecture", return_value="arm64"):
                worker.execute(request, lambda event: None)
                self.assertEqual(install.call_args.args[0]["tag"], "v2.0.0")
                self.assertFalse(install.call_args.kwargs["open_installer"])
                install.reset_mock()
                request["config"]["packages"][0]["local"] = "3.0.0"
                with self.assertRaises(ValueError):
                    worker.execute(request, lambda event: None)
                install.assert_not_called()
