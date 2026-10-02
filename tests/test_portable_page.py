from pathlib import Path
import json
from types import SimpleNamespace
import time
import tempfile
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

    def test_text_edits_are_debounced_and_saved_without_button(self):
        self.page.variables["preserve"].set("config")
        self.page.variables["preserve"].set("config.yaml;data")
        self.page.app.save.assert_not_called()
        time.sleep(0.65)
        self.window.update()
        self.page.app.save.assert_called_once()
        self.assertEqual(self.profiles[0]["preserve"], "config.yaml;data")
        self.assertEqual(self.page.save_status.get(), "配置已自动保存")
        self.assertNotIn("保存配置", [w.cget("text") for w, _ in self.page.widgets if isinstance(w, ttk.Button)])

    def test_repository_requires_double_click_and_relocks_after_commit_or_switch(self):
        entry = self.page.repository_entry
        self.assertEqual(str(entry.cget("state")), "readonly")
        self.page.edit_repository()
        self.assertEqual(str(entry.cget("state")), "normal")
        self.page.variables["repository"].set("https://github.com/owner/new")
        self.page.finish_repository_edit()
        self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/new")
        self.assertEqual(str(entry.cget("state")), "readonly")
        self.page.edit_repository()
        self.page.select(index=1)
        self.assertEqual(str(entry.cget("state")), "readonly")
        self.page.busy = True
        self.page.edit_repository()
        self.assertEqual(str(entry.cget("state")), "readonly")

    def test_invalid_repository_remains_editable_after_attempted_commit(self):
        self.page.edit_repository()
        self.page.variables["repository"].set("invalid")
        self.page.finish_repository_edit()
        self.assertEqual(str(self.page.repository_entry.cget("state")), "normal")
        self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/a")

    def test_repository_auto_save_rejects_incomplete_and_duplicate_addresses(self):
        for address in ("https://github.com/owner/", "https://github.com/owner/b"):
            self.page.variables["repository"].set(address)
            self.page.auto_save()
            self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/a")
            self.assertIn("未保存", self.page.save_status.get())
        self.page.app.save.assert_not_called()
        self.page.variables["repository"].set("https://github.com/owner/new")
        self.page.auto_save()
        self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/new")
        self.page.app.save.assert_called_once()
        self.page.check.assert_not_called()

    def test_row_switch_flushes_pending_edit_without_saving_it_to_next_row(self):
        self.page.variables["preserve"].set("data")
        self.page.select(index=1)
        self.assertEqual(self.profiles[0]["preserve"], "data")
        self.assertEqual(self.profiles[1]["preserve"], "")
        self.assertIsNone(self.page.save_timer)
        self.page.app.save.reset_mock()
        time.sleep(0.65)
        self.window.update()
        self.page.app.save.assert_not_called()

    def test_auto_save_failure_is_visible_without_modal_and_can_retry(self):
        self.page.variables["preserve"].set("data")
        self.page.app.save.side_effect = OSError("locked")
        with patch("cpa_manager.ui.pages.portable.messagebox.showerror") as dialog:
            self.page.auto_save()
        dialog.assert_not_called()
        self.assertIn("自动保存失败", self.page.save_status.get())
        self.page.app.save.side_effect = None
        self.page.variables["preserve"].set("data;config.yaml")
        self.page.auto_save()
        self.assertEqual(self.page.save_status.get(), "配置已自动保存")

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

    def test_directory_edit_preserves_identity_and_catalog_and_reads_new_local_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            original = old / "keep.txt"
            original.write_text("keep", encoding="utf-8")
            self.page.variables["directory"].set(str(old))
            self.page.persist()
            profile = self.page.profile
            profile.update(selected_version="v2", selected_asset="tool.zip")
            catalog = [{"tag": "v2", "assets": [{"name": "tool.zip", "size": 2}]}]
            self.page.row_catalogs[profile["id"]] = catalog
            (new / ".github-install.json").write_text(json.dumps({"repository": profile["repository"], "version": "v1"}), encoding="utf-8")
            self.assertEqual(self.page.address_value(profile["id"], "directory"), str(old))
            self.assertTrue(self.page.address_commit(profile["id"], "directory", str(new)))
            self.assertIs(self.page.profile, profile)
            self.assertEqual(self.page.table.set(profile["id"], "directory"), str(new.resolve()))
            self.assertEqual(self.page.table.set(profile["id"], "local"), "v1")
            self.assertEqual(profile["selected_version"], "v2")
            self.assertEqual(profile["selected_asset"], "tool.zip")
            self.assertIs(self.page.row_catalogs[profile["id"]], catalog)
            self.assertEqual(original.read_text(encoding="utf-8"), "keep")
            self.page.check.assert_not_called()

    def test_invalid_directory_and_failed_save_keep_previous_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.page.variables["directory"].set(directory)
            self.page.persist()
            occupied = Path(directory) / "file.txt"
            occupied.touch()
            for value in ("", str(occupied)):
                self.assertFalse(self.page.change_directory(value))
                self.assertEqual(self.page.profile["directory"], directory)
            self.page.app.save.side_effect = OSError("locked")
            with patch("cpa_manager.ui.pages.portable.messagebox.showerror"):
                self.assertFalse(self.page.change_directory(str(Path(directory) / "next")))
            self.assertEqual(self.page.profile["directory"], directory)
            self.assertEqual(self.page.variables["directory"].get(), directory)

    def test_choose_directory_targets_selected_row_and_cancel_keeps_it(self):
        self.page.table.selection_set("1")
        self.page.table_selected()
        with tempfile.TemporaryDirectory() as directory:
            with patch("cpa_manager.ui.pages.portable.filedialog.askdirectory", return_value=directory):
                self.page.choose_directory()
            self.assertEqual(self.profiles[1]["directory"], str(Path(directory).resolve()))
            self.assertEqual(self.profiles[0]["directory"], "")
            with patch("cpa_manager.ui.pages.portable.filedialog.askdirectory", return_value=""):
                self.page.choose_directory()
            self.assertEqual(self.profiles[1]["directory"], str(Path(directory).resolve()))

    def test_backup_cleanup_uses_selected_directory_and_requires_confirmation(self):
        self.page.start = Mock()
        self.page.table.selection_set("1")
        self.page.table_selected()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.page.variables["directory"].set(str(root))
            self.page.persist()
            backup = root / (".install-backup-" + "a" * 32)
            backup.mkdir()
            with patch("cpa_manager.ui.pages.portable.messagebox.askyesno", return_value=False):
                self.page.clear_backups()
            self.page.start.assert_not_called()
            with patch("cpa_manager.ui.pages.portable.messagebox.askyesno", return_value=True):
                self.page.clear_backups()
            task = self.page.start.call_args.args[0]
            from cpa_manager.core.download import DownloadControl
            task(Mock(), DownloadControl())
            self.assertFalse(backup.exists())
            self.assertIs(self.page.profile, self.profiles[1])
            self.page.check.assert_not_called()

    def test_fixed_actions_use_clicked_portable_row_and_do_not_run_while_busy(self):
        self.page.install = Mock()
        self.page.open_folder = Mock()
        self.page.clear_backups = Mock()
        for action, command in (("install", self.page.install), ("open", self.page.open_folder), ("clear", self.page.clear_backups)):
            self.page.row_actions.activate("1", action)
            command.assert_called_once()
            self.assertIs(self.page.profile, self.profiles[1])
        self.page.busy = True
        self.page.row_actions.activate("0", "open")
        self.page.open_folder.assert_called_once()
        self.assertIs(self.page.profile, self.profiles[1])
        self.page.check.assert_not_called()

    def test_fixed_action_does_not_run_on_previous_row_when_selection_save_fails(self):
        self.page.open_folder = Mock()
        self.page.variables["repository"].set("invalid")
        self.page.row_actions.activate("1", "open")
        self.page.open_folder.assert_not_called()
        self.assertIs(self.page.profile, self.profiles[0])

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
        self.assertEqual(self.page.asset_selector.get(), "tool-x86.zip")

    def test_lower_package_selector_updates_table_and_survives_row_switch(self):
        release = {"repository": self.profiles[0]["repository"], "tag": "v1", "notes": "",
                   "assets": [{"name": "tool-x64.zip", "size": 1024}, {"name": "tool-x86.zip", "size": 2048}]}
        self.page.events.put(("catalog", [release]))
        self.page.poll()
        self.page.asset_selector.set("tool-x86.zip")
        self.page.asset_selector.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.profiles[0]["selected_asset"], "tool-x86.zip")
        self.assertEqual(self.page.table.set("0", "package"), "tool-x86.zip")
        self.assertEqual(self.page.table.set("0", "size"), "2.00 KB")
        self.page.select(index=1)
        self.assertEqual(self.page.asset_selector.get(), "")
        self.assertEqual(len(self.page.asset_selector["values"]), 0)
        self.page.select(index=0)
        self.assertEqual(self.page.asset_selector.get(), "tool-x86.zip")
        self.page.check.assert_not_called()

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

    def test_external_install_record_change_invalidates_local_version_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            self.page.variables["directory"].set(directory)
            self.page.persist()
            metadata = Path(directory) / ".github-install.json"
            repo = self.profiles[0]["repository"]
            metadata.write_text(json.dumps({"repository": repo, "version": "v1"}), encoding="utf-8")
            self.page.update_labels(self.profiles[0])
            self.assertEqual(self.page.table.set("0", "local"), "v1")
            metadata.write_text(json.dumps({"repository": repo, "version": "v2-longer"}), encoding="utf-8")
            self.page.select(index=1)
            self.page.select(index=0)
            self.assertEqual(self.page.table.set("0", "local"), "v2-longer")
            metadata.unlink()
            self.page.update_labels(self.profiles[0])
            self.assertEqual(self.page.table.set("0", "local"), "尚无记录")
            self.page.check.assert_not_called()

    def test_recreated_page_uses_catalog_instead_of_stale_latest_scalar(self):
        release = {"repository": self.profiles[0]["repository"], "tag": "v2", "notes": "",
                   "assets": [{"name": "tool.zip", "url": "https://github.com/owner/a/releases/download/v2/tool.zip"}]}
        self.page.cache.put(release["repository"], [release])
        self.profiles[0]["latest_version"] = "v1"
        restored = PortablePage(self.page.app, ttk.Notebook(self.window), Path.cwd())
        self.assertEqual(restored.table.set("0", "latest"), "v2")
        self.assertIsNone(restored.fetch_timer)


if __name__ == "__main__":
    unittest.main()
