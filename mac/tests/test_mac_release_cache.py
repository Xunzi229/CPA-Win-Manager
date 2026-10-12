import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpa_mac.backends.release_cache import ReleaseCache, valid_catalog, valid_release
from cpa_mac.backends import release_selection


def sample_catalog():
    return [
        {
            "repository": "https://github.com/2dust/v2rayN",
            "tag": "7.25.4",
            "prerelease": False,
            "assets": [
                {
                    "name": "v2rayN-macos-arm64.dmg",
                    "url": "https://github.com/2dust/v2rayN/releases/download/7.25.4/v2rayN-macos-arm64.dmg",
                    "size": 52428800,
                    "digest": ""
                }
            ]
        },
        {
            "repository": "https://github.com/2dust/v2rayN",
            "tag": "7.24.9",
            "prerelease": False,
            "assets": [
                {
                    "name": "v2rayN-macos-arm64.dmg",
                    "url": "https://github.com/2dust/v2rayN/releases/download/7.24.9/v2rayN-macos-arm64.dmg",
                    "size": 51200000,
                    "digest": ""
                }
            ]
        }
    ]


class TestMacReleaseCache(unittest.TestCase):
    def test_cache_validation(self):
        catalog = sample_catalog()
        self.assertTrue(valid_catalog(catalog, "https://github.com/2dust/v2rayN"))
        self.assertFalse(valid_catalog([], "https://github.com/2dust/v2rayN"))
        self.assertFalse(valid_catalog([{"tag": "1.0"}], "https://github.com/2dust/v2rayN"))

    def test_cache_persistence_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir) / ".release-cache"
            cache = ReleaseCache(cache_dir)
            repo = "https://github.com/2dust/v2rayN"
            catalog = sample_catalog()

            # Initially empty
            self.assertEqual(cache.get(repo), [])

            # Put and flush to disk
            self.assertTrue(cache.put(repo, catalog))
            self.assertTrue(cache.dirty)
            cache.flush()
            self.assertFalse(cache.dirty)

            # Check file exists on disk
            json_files = list(cache_dir.glob("*.json"))
            self.assertEqual(len(json_files), 1)

            # Simulate app restart by creating a brand-new ReleaseCache instance pointing to same directory
            restarted_cache = ReleaseCache(cache_dir)
            loaded = restarted_cache.get(repo)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["tag"], "7.25.4")
            self.assertEqual(loaded[1]["tag"], "7.24.9")
            self.assertEqual(loaded[0]["assets"][0]["name"], "v2rayN-macos-arm64.dmg")

    def test_fetch_catalog_uses_cache_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir) / ".release-cache"
            cache = ReleaseCache(cache_dir)
            release_selection.set_release_cache(cache)
            repo = "https://github.com/2dust/v2rayN"

            # Mock remote GitHub API response for check/fetch_catalog
            api_response = json.dumps([
                {
                    "tag_name": "7.25.4",
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "v2rayN-macos-arm64.dmg",
                            "browser_download_url": "https://github.com/2dust/v2rayN/releases/download/7.25.4/v2rayN-macos-arm64.dmg",
                            "size": 52428800
                        }
                    ]
                },
                {
                    "tag_name": "7.24.9",
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "v2rayN-macos-arm64.dmg",
                            "browser_download_url": "https://github.com/2dust/v2rayN/releases/download/7.24.9/v2rayN-macos-arm64.dmg",
                            "size": 51200000
                        }
                    ]
                }
            ])

            with patch("cpa_mac.backends.github.read_text", return_value=api_response) as mock_read:
                # First check fetches from remote and caches
                catalog = release_selection.fetch_catalog(repo, force=True)
                self.assertEqual(len(catalog), 2)
                self.assertEqual(catalog[0]["tag"], "7.25.4")
                self.assertTrue(mock_read.called)

            # Simulate restart
            release_selection.set_release_cache(ReleaseCache(cache_dir))

            # Without network call, fetch_catalog, list_releases, and fetch_release all hit disk cache!
            with patch("cpa_mac.backends.github.read_text", side_effect=RuntimeError("Network offline")):
                cached_catalog = release_selection.fetch_catalog(repo, force=False)
                self.assertEqual(len(cached_catalog), 2)
                self.assertEqual(cached_catalog[0]["tag"], "7.25.4")

                # Check worker release_options uses disk cache without network!
                from cpa_mac import native_worker as worker
                request = {
                    "scope": "software", "mode": "package", "action": "release_options",
                    "source_root": "/tmp",
                    "config": {"packages": [{"id": "pkg1", "repository": repo}], "prerelease": False, "proxy_enabled": False, "proxy": "", "download_workers": 4},
                    "body": {"id": "pkg1", "repository": repo, "page": 1}
                }
                res = worker.execute(request)
                self.assertEqual(len(res["releases"]), 2)
                self.assertEqual(res["releases"][0]["tag"], "7.25.4")

                # Check worker release_assets uses disk cache without network!
                req_assets = {
                    "scope": "software", "mode": "package", "action": "release_assets",
                    "source_root": "/tmp",
                    "config": {"packages": [{"id": "pkg1", "repository": repo}], "prerelease": False, "proxy_enabled": False, "proxy": "", "download_workers": 4},
                    "body": {"id": "pkg1", "repository": repo, "tag": "7.24.9"}
                }
                with patch("cpa_mac.native_worker.host_architecture", return_value="arm64"):
                    res_assets = worker.execute(req_assets)
                self.assertEqual(res_assets["tag"], "7.24.9")
                self.assertEqual(res_assets["assets"][0]["name"], "v2rayN-macos-arm64.dmg")


if __name__ == "__main__":
    unittest.main()
