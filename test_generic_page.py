from pathlib import Path
from types import SimpleNamespace
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from generic_page import GenericPage
from manager import App
from runtime_utils import default_download_directory


class SoftwareSwitchTests(unittest.TestCase):
    def setUp(self):
        self.window = tk.Tk()
        self.window.withdraw()
        self.profiles = [{"name": name, "repository": repo, "mode": "便携安装"}
                         for name, repo in (("A", "https://github.com/owner/a"),
                                           ("B", "https://github.com/owner/b"), ("Empty", ""))]
        app = SimpleNamespace(window=self.window, custom_profiles=self.profiles,
                              manager_busy=False, save=Mock())
        self.page = GenericPage(app, ttk.Notebook(self.window), Path.cwd())
        self.page.check = Mock()

    def tearDown(self):
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()

    def flush_timer(self):
        time.sleep(0.3)
        self.window.update()

    def test_switch_fetches_selected_software(self):
        self.page.names.set("B")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_called_once()
        self.assertEqual(self.page.variables["repository"].get(), "https://github.com/owner/b")

    def test_rapid_switch_queries_only_last_selection(self):
        self.page.names.set("B")
        self.page.select()
        self.page.names.set("A")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_called_once()
        self.assertEqual(self.page.profile["name"], "A")

    def test_empty_repository_cancels_pending_query(self):
        self.page.names.set("B")
        self.page.select()
        self.page.names.set("Empty")
        self.page.select()
        self.flush_timer()
        self.page.check.assert_not_called()

    def test_portable_records_display_and_select_by_directory(self):
        first = str(Path.cwd() / "apps-one" / "same-name")
        second = str(Path.cwd() / "apps-two" / "same-name")
        self.profiles[0]["directory"] = first
        self.profiles[1]["directory"] = second
        self.page.profile = None
        self.page.refresh_names(auto_fetch=False)
        self.assertEqual(self.page.selector["values"][:2], (first, second))
        self.page.choose_portable(second)
        self.assertIs(self.page.profile, self.profiles[1])
        self.assertEqual(self.page.target(), Path(second).resolve())
        self.assertEqual(self.page.variables["repository"].get(), "https://github.com/owner/b")
        self.assertEqual(len(self.profiles), 3)

    def test_new_portable_directory_creates_independent_record(self):
        directory = str(Path.cwd() / "new-software")
        self.page.choose_portable(directory)
        self.assertEqual(self.page.names.get(), directory)
        self.assertEqual(self.page.profile["directory"], directory)
        self.assertEqual(self.page.variables["repository"].get(), "")
        self.assertEqual(self.profiles[0]["repository"], "https://github.com/owner/a")

    def test_installer_uses_custom_name_and_hides_preserve_row(self):
        with patch("generic_page.simpledialog.askstring", return_value="自定义安装器"):
            self.page.add("安装器")
        self.assertEqual(self.page.names.get(), "自定义安装器")
        self.assertEqual(self.page.variables["mode"].get(), "安装器")
        self.assertFalse(self.page.preserve_row.winfo_manager())
        self.page.variables["preserve"].set("data;config.yaml")
        self.page.variables["mode"].set("便携安装")
        self.assertEqual(self.page.preserve_row.winfo_manager(), "pack")
        self.assertEqual(self.page.variables["preserve"].get(), "data;config.yaml")

    def test_choosing_installer_download_directory_keeps_identity(self):
        self.profiles[0]["mode"] = "安装器"
        self.page.profile = None
        self.page.refresh_names(auto_fetch=False)
        directory = str(Path.cwd() / "installer-downloads")
        self.page.app.installer_download_directory = directory
        self.page.persist()
        self.assertEqual(self.page.names.get(), "A")
        self.assertEqual(self.page.target(), Path(directory).resolve())
        self.assertEqual(self.page.app.installer_download_directory, directory)
        self.assertNotIn("directory", self.page.profile)
        self.assertEqual(len(self.profiles), 3)

    def test_installer_directory_is_shared_across_software(self):
        self.profiles[0]["mode"] = self.profiles[1]["mode"] = "安装器"
        self.page.profile = None
        self.page.refresh_names(auto_fetch=False)
        directory = str(Path.cwd() / "shared-downloads")
        self.page.app.installer_download_directory = directory
        self.page.persist()
        self.page.selector.current(1)
        self.page.select(auto_fetch=False)
        self.assertEqual(self.page.target(), Path(directory).resolve())
        self.assertEqual(self.page.app.installer_download_directory, directory)

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


if __name__ == "__main__":
    unittest.main()
