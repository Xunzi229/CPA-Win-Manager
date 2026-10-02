"""Regression coverage for validation, cache migration and cancellation boundaries."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cpa_manager.app import App
from cpa_manager.backends import github, installer
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.download import DownloadControl, DownloadCancelled, check_cancel
from cpa_manager.core.models import ensure_ids


def release():
    return {"repository": "https://github.com/Owner/Tool", "tag": "v1", "notes": "",
            "assets": [{"name": "tool.exe", "url": "https://github.com/owner/tool/releases/download/v1/tool.exe", "size": 3}]}


class OptimizationTests(unittest.TestCase):
    def test_case_variants_are_accepted_but_other_repositories_are_rejected(self):
        source = release()
        data = {"tag_name": "v1", "assets": [{"name": "tool.exe", "browser_download_url": source["assets"][0]["url"]}]}
        self.assertEqual(len(github.release_data("https://github.com/OWNER/TOOL", data)["assets"]), 1)
        for url in ("https://github.com/other/tool/releases/download/v1/tool.exe",
                    "https://github.com.evil/owner/tool/releases/download/v1/tool.exe", "https://["):
            self.assertFalse(github.asset_url_matches(url, source["repository"]))

    def test_bad_cache_does_not_remove_profile_or_break_initialization(self):
        for cached in ({"tag": "v1"}, {"assets": None}, [], {**release(), "assets": [None]},
                       {**release(), "assets": [{"name": "bad", "url": "https://["}]}):
            profile = installer.load_profiles([{"id": "stable", "name": "Tool", "repository": release()["repository"], "release": cached}])[0]
            self.assertEqual(profile["id"], "stable")
            self.assertIsNone(profile["release"])
            self.assertIsNone(installer.selected_asset(profile))

    def test_ids_survive_reload_and_duplicates_are_repaired(self):
        profiles = [{"id": "same"}, {"id": "same"}, {}]
        ensure_ids(profiles)
        ids = [p["id"] for p in profiles]
        ensure_ids(profiles)
        self.assertEqual(ids, [p["id"] for p in profiles])
        self.assertEqual(len(set(ids)), 3)

    def test_cache_is_separate_reusable_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ReleaseCache(directory)
            self.assertTrue(cache.put(release()["repository"], [release()]))
            cache.flush()
            restored = ReleaseCache(directory)
            self.assertEqual(restored.get("https://github.com/OWNER/TOOL"), [release()])
            cache.put(release()["repository"], [release()])
            self.assertFalse(cache.dirty)
            changed = copy.deepcopy(release())
            changed["tag"] = "v2"
            cache.put(changed["repository"], [changed])
            with patch("cpa_manager.backends.release_cache.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    cache.flush()
            self.assertEqual(ReleaseCache(directory).get(changed["repository"])[0]["tag"], "v1")

    def test_unchanged_settings_are_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            app = SimpleNamespace(profiles={}, proxy_settings={}, custom_profiles=[], installer_profiles=[],
                                  installer_download_directory=directory, release_cache=ReleaseCache(),
                                  settings_file=Path(directory) / "settings.json")
            App.save(app)
            with patch("cpa_manager.app.os.replace") as replace:
                App.save(app)
                replace.assert_not_called()

    def test_settings_preserve_unmigrated_installers_and_separate_release_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ReleaseCache(Path(directory) / "cache")
            cache.put(release()["repository"], [release()])
            pending = {"name": "Legacy", "mode": "安装器", "repository": ""}
            app = SimpleNamespace(profiles={}, proxy_settings={},
                                  custom_profiles=[{"name": "Tool", "release_catalog": [release()]}],
                                  pending_legacy_installers=[pending], installer_profiles=[],
                                  installer_download_directory=directory, release_cache=cache,
                                  settings_file=Path(directory) / "settings.json")
            App.save(app)
            saved = json.loads(app.settings_file.read_text(encoding="utf-8"))
            self.assertNotIn("release_catalog", saved["custom_software"][0])
            self.assertEqual(saved["custom_software"][1], pending)
            self.assertEqual(ReleaseCache(cache.directory).get(release()["repository"]), [release()])

    def test_paused_download_can_be_cancelled_but_commit_cannot(self):
        control = DownloadControl()
        control.pause()
        self.assertTrue(control.request_cancel())
        self.assertFalse(control.paused.is_set())
        with self.assertRaises(DownloadCancelled):
            check_cancel(control)
        control = DownloadControl()
        control.begin_commit()
        self.assertFalse(control.request_cancel())
        self.assertFalse(control.is_set())
        control.finish()
        self.assertTrue(control.request_cancel())

    def test_close_requests_stop_and_waits_for_worker(self):
        page = SimpleNamespace(busy=True, request_stop=Mock(), persist=Mock(return_value=True))
        app = SimpleNamespace(manager_busy=False, pages=[page], window=Mock(), manager_status=Mock(), proxy_dialog=None)
        app.close = lambda: App.close(app)
        App.close(app)
        page.request_stop.assert_called_once()
        app.window.destroy.assert_not_called()
        page.busy = False
        App.close(app)
        app.window.destroy.assert_called_once()
