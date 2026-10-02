from pathlib import Path
import json
from types import SimpleNamespace
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from cpa_manager.ui.pages.portable import PortablePage
from cpa_manager.app import App
from cpa_manager.core.runtime import default_download_directory


class SoftwareSwitchTests(unittest.TestCase):
    def setUp(self):
        self.window = tk.Tk()
        self.window.withdraw()
        self.profiles = [{"name": name, "repository": repo, "mode": "便携安装"}
                         for name, repo in (("A", "https://github.com/owner/a"),
                                           ("B", "https://github.com/owner/b"), ("Empty", ""))]
        for i, profile in enumerate(self.profiles):
            profile["id"] = str(i)
        app = SimpleNamespace(window=self.window, custom_profiles=self.profiles,
                              manager_busy=False, save=Mock())
        self.page = PortablePage(app, ttk.Notebook(self.window), Path.cwd())
        self.page.check = Mock()
        self.page.cancel_event.clear()

    def tearDown(self):
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()

    def flush_timer(self):
        time.sleep(0.3)
        self.window.update()

    def test_switch_does_not_fetch_selected_software(self):
        self.page.names.set("B")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_not_called()
        self.assertEqual(self.page.variables["repository"].get(), "https://github.com/owner/b")

    def test_rapid_switch_does_not_query_releases(self):
        self.page.names.set("B")
        self.page.select()
        self.page.names.set("A")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_not_called()
        self.assertEqual(self.page.profile["name"], "A")

    def test_empty_repository_cancels_pending_query(self):
        self.page.names.set("B")
        self.page.select()
        self.page.names.set("Empty")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_not_called()

    def test_switch_reuses_local_versions_without_saving_settings(self):
        for profile in self.profiles:
            profile["directory"] = str(Path.cwd())
        self.page.variables["directory"].set(str(Path.cwd()))
        self.page.select(index=0)
        self.page.persist()
        with patch("cpa_manager.ui.pages.portable.backend.local_version", return_value="v1") as local:
            self.page.local_versions.clear()
            self.page.update_labels()
            self.assertEqual(local.call_count, 2)
            local.reset_mock()
            self.page.app.save.reset_mock()
            for index in (1, 0, 1, 0):
                self.page.select(index=index)
            local.assert_not_called()
            self.page.app.save.assert_not_called()

    def test_portable_directory_can_be_added_again_as_an_independent_row(self):
        first = str(Path.cwd() / "apps-one" / "same-name")
        second = str(Path.cwd() / "apps-two" / "same-name")
        self.profiles[0]["directory"] = first
        self.profiles[1]["directory"] = second
        self.page.profile = None
        self.page.refresh_names(auto_fetch=False)
        self.assertEqual(self.page.selector["values"][:2], (first, second))
        self.page.choose_portable(second)
        self.assertIs(self.page.profile, self.profiles[3])
        self.assertEqual(self.page.target(), Path(second).resolve())
        self.assertEqual(self.page.variables["repository"].get(), "")
        self.assertEqual(self.profiles[1]["repository"], "https://github.com/owner/b")
        self.assertEqual(len(self.profiles), 4)

    def test_repository_uniqueness_ignores_case_and_release_url_variants(self):
        self.page.selector.current(2)
        self.page.select(auto_fetch=False)
        for address in ("https://github.com/OWNER/A/releases/latest", "https://github.com/owner/a.git"):
            self.page.variables["repository"].set(address)
            self.assertFalse(self.page.persist())
            self.assertEqual(self.profiles[2]["repository"], "")
        self.page.variables["repository"].set("https://github.com/owner/unique")
        self.assertTrue(self.page.persist())
        self.assertEqual(self.profiles[2]["repository"], "https://github.com/owner/unique")

    def test_inline_address_edit_rejects_duplicate_then_saves_new_repository(self):
        self.assertEqual(self.page.address_value("1", "repository"), self.profiles[1]["repository"])
        self.assertFalse(self.page.address_commit("1", "repository", "https://github.com/OWNER/A/releases/latest"))
        self.assertEqual(self.profiles[1]["repository"], "https://github.com/owner/b")
        self.assertTrue(self.page.address_commit("1", "repository", "https://github.com/owner/new"))
        self.assertEqual(self.page.table.set("1", "repository"), "https://github.com/owner/new")
        self.page.check.assert_not_called()

    def test_duplicate_empty_directory_records_select_and_remove_by_row(self):
        directory = str(Path.cwd() / "shared-root")
        self.page.choose_portable(directory)
        first = self.page.profile
        self.page.choose_portable(directory)
        second = self.page.profile
        self.assertIsNot(first, second)
        self.assertNotEqual(first["id"], second["id"])
        self.page.table.selection_set(first["id"])
        self.page.table_selected()
        self.assertIs(self.page.profile, first)
        with patch("cpa_manager.ui.pages.portable.messagebox.askyesno", return_value=True):
            self.page.remove()
        self.assertFalse(any(p is first for p in self.profiles))
        self.assertTrue(any(p is second for p in self.profiles))

    def test_new_portable_directory_creates_independent_record(self):
        directory = str(Path.cwd() / "new-software")
        self.page.choose_portable(directory)
        self.assertEqual(self.page.names.get(), directory)
        self.assertEqual(self.page.profile["directory"], directory)
        self.assertEqual(self.page.variables["repository"].get(), "")
        self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/a")




    def test_settings_directory_survives_profile_save_and_failed_save_rolls_back(self):
        app = self.page.app
        app.proxy_settings = {"enabled": False, "url": ""}
        app.proxy_status = Mock()
        App.set_settings(app, False, "", "")
        self.assertEqual(Path(app.installer_download_directory), default_download_directory().resolve())
        directory = str(Path.cwd() / "downloads-from-settings")
        App.set_settings(app, False, "", directory)
        self.page.persist()
        self.assertEqual(app.installer_download_directory, directory)
        app.save.side_effect = OSError("save failed")
        with self.assertRaises(OSError):
            App.set_settings(app, True, "http://127.0.0.1:7890", str(Path.cwd() / "other-downloads"))
        self.assertEqual(app.installer_download_directory, directory)
        self.assertEqual(app.proxy_settings, {"enabled": False, "url": ""})

    def test_pause_resume_and_option_change_reset_state(self):
        self.assertFalse(self.page.pause_button.winfo_manager())
        self.assertEqual(self.page.pause_button.master, self.page.progress.master)
        self.page.cancel_event.clear()
        self.page.downloading = True
        self.page.pause_button.pack(side="left")
        self.page.progress.configure(value=30)
        self.page.write("old download")
        self.page.toggle_pause()
        self.assertTrue(self.page.cancel_event.paused.is_set())
        self.assertEqual(self.page.pause_button["text"], "▶")
        self.page.toggle_pause()
        self.assertFalse(self.page.cancel_event.paused.is_set())
        self.assertEqual(self.page.pause_button["text"], "⏸")
        old_generation = self.page.generation
        self.page.asset_changed()
        self.assertFalse(self.page.pause_button.winfo_manager())
        self.assertEqual(float(self.page.progress["value"]), 0)
        self.assertEqual(self.page.log.get("1.0", "end").strip(), "")
        self.page.events.put(("progress", (60, "stale progress"), old_generation))
        self.page.poll()
        self.assertEqual(float(self.page.progress["value"]), 0)
        self.assertNotEqual(self.page.status.get(), "stale progress")

    def test_release_query_auto_selects_matching_package(self):
        assets = [{"name": name, "size": 1024} for name in
                  ("tool-windows-arm64.zip", "tool-windows-x64.zip", "tool-linux-x64.zip")]
        release = {"repository": "https://github.com/owner/a", "tag": "v1", "notes": "", "assets": assets}
        self.page.events.put(("release", release))
        with patch("cpa_manager.backends.github.windows_architecture", return_value="amd64"):
            self.page.poll()
        self.assertTrue(self.page.asset.get().startswith("tool-windows-x64.zip"))
        self.assertGreaterEqual(self.page.asset_selector.current(), 0)

    def test_inline_version_updates_package_without_changing_latest(self):
        releases = [{"repository": "https://github.com/owner/a", "tag": tag, "notes": "",
                     "assets": [{"name": f"tool-windows-x64-{tag}.zip", "size": 1024}]}
                    for tag in ("v2", "v1")]
        self.page.events.put(("catalog", releases))
        with patch("cpa_manager.backends.github.windows_architecture", return_value="amd64"):
            self.page.poll()
            self.page.inline_commit("0", "version", "v1")
        self.assertEqual(self.page.release["tag"], "v1")
        self.assertEqual(self.page.profile["latest_version"], "v2")
        self.assertEqual(self.page.profile["selected_version"], "v1")
        self.assertIn("v1.zip", self.page.table.set("0", "package"))
        self.assertIn("KB", self.page.table.set("0", "size"))
        self.assertEqual(self.page.table.set("0", "package"), "tool-windows-x64-v1.zip")
        self.assertEqual(self.page.table.set("0", "latest"), "v2")

    def test_context_menu_targets_right_clicked_row(self):
        menu = Mock()
        event = SimpleNamespace(x=10, y=10, x_root=20, y_root=20)
        with patch.object(self.page.table, "identify_row", return_value="1"), \
             patch("cpa_manager.ui.pages.portable.tk.Menu", return_value=menu):
            self.page.context_menu(event)
        self.assertIs(self.page.profile, self.profiles[1])
        self.assertEqual(self.page.table.selection(), ("1",))
        commands = {call.kwargs["label"]: call.kwargs["command"] for call in menu.add_command.call_args_list}
        commands["检查更新 / 获取版本"]()
        self.page.check.assert_called_once()
        self.assertIsNone(self.page.fetch_timer)

    def test_clicking_uncached_rows_and_cells_does_not_fetch_versions(self):
        for row in ("1", "0", "1"):
            self.page.table.selection_set(row)
            self.page.table_selected()
            for key in ("version", "package"):
                self.page.inline_choices(row, key)
            self.assertIsNone(self.page.fetch_timer)
        self.flush_timer()
        self.page.check.assert_not_called()
        self.assertIs(self.page.profile, self.profiles[1])

    def test_inline_package_choice_is_saved_on_its_row(self):
        release = {"repository": "https://github.com/owner/a", "tag": "v1", "notes": "",
                   "assets": [{"name": "tool-x64.zip", "size": 1024}, {"name": "tool-x86.zip", "size": 2048}]}
        self.page.events.put(("catalog", [release]))
        self.page.poll()
        values, _ = self.page.inline_choices("0", "package")
        choice = next(v for v in values if "x86" in v)
        self.page.inline_commit("0", "package", choice)
        self.assertEqual(self.profiles[0]["selected_asset"], "tool-x86.zip")
        self.assertIn("tool-x86.zip", self.page.table.set("0", "package"))
        self.assertEqual(self.page.table.set("0", "size"), "2.00 KB")
        self.assertNotIn("selected_asset", self.profiles[1])

    def test_catalog_keeps_latest_first_and_filters_drafts_and_empty_releases(self):
        from cpa_manager.backends import github as generic_backend
        repo = "https://github.com/owner/a"
        def published(tag, **extra):
            return dict(tag_name=tag, body="", assets=[{"name": "tool-x64.zip",
                        "browser_download_url": repo + "/releases/download/" + tag + "/tool-x64.zip"}], **extra)
        latest = published("v2")
        listing = [published("v3-beta", prerelease=True), latest, published("draft", draft=True),
                   {"tag_name": "empty", "assets": []}, published("v1")]
        with patch("cpa_manager.backends.github.network"), patch("cpa_manager.backends.github.read_text",
                side_effect=[json.dumps(latest), json.dumps(listing)]):
            catalog = generic_backend.release_catalog(repo, "")
        self.assertEqual([r["tag"] for r in catalog], ["v2", "v3-beta", "v1"])

    def test_cached_catalog_survives_switch_and_page_recreation(self):
        release = {"repository": self.profiles[0]["repository"], "tag": "v2", "notes": "",
                   "assets": [{"name": "tool.zip", "url": "https://github.com/owner/a/releases/download/v2/tool.zip", "size": 1024}]}
        self.page.events.put(("catalog", [release]))
        self.page.poll()
        self.page.selector.current(1)
        self.page.select()
        self.page.selector.current(0)
        self.page.select()
        self.flush_timer()
        self.page.check.assert_not_called()
        self.assertEqual(self.page.release["tag"], "v2")
        self.assertEqual(self.page.cache.get(self.profiles[0]["repository"])[0]["tag"], "v2")
        self.assertNotIn("release_catalog", self.profiles[0])
        restored = PortablePage(self.page.app, ttk.Notebook(self.window), Path.cwd())
        self.assertEqual(restored.release["tag"], "v2")
        self.assertEqual(restored.table.set("0", "size"), "1.00 KB")
        self.assertIsNone(restored.fetch_timer)

    def test_manual_check_refreshes_despite_cached_catalog(self):
        self.page.catalog = [{"tag": "v1"}]
        self.page.app.proxy_url = lambda: ""
        self.page.start = Mock()
        PortablePage.check(self.page)
        self.page.start.assert_called_once()

    def test_all_portable_columns_are_centered(self):
        for column in self.page.table["columns"]:
            self.assertEqual(str(self.page.table.column(column, "anchor")), "center")
            self.assertEqual(str(self.page.table.heading(column, "anchor")), "center")


if __name__ == "__main__":
    unittest.main()
