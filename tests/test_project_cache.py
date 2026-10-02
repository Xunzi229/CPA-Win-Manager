"""Project release cache survives installs and reconciles with newer binaries."""
from pathlib import Path
from types import SimpleNamespace
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from cpa_manager.config import default_profiles
from cpa_manager.ui.pages.project import ProjectPage


class ProjectCacheTests(unittest.TestCase):
    def setUp(self):
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = SimpleNamespace(window=self.window, profiles=default_profiles(),
                                   update_dot="", save=Mock(), closing=False)
        self.notebook = ttk.Notebook(self.window)

    def tearDown(self):
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()

    def page(self, key):
        with patch.object(ProjectPage, "schedule_refresh"):
            page = ProjectPage(self.app, self.notebook, key, smoke=True)
        page.smoke = False
        page.run = Mock()
        return page

    def test_newer_local_binary_refreshes_same_day_cache_only_once(self):
        for key in ("cli", "plus"):
            with self.subTest(project=key):
                page = self.page(key)
                page.remote_version = "v1.0.0"
                self.app.profiles[key]["checks"][str(page.target()).lower()] = time.strftime("%Y-%m-%d")
                for _ in range(2):
                    page.events.put(("snapshot", page.generation, (False, "停止", "1.0.2", "", "")))
                    page.poll()
                    page.daily_check()
                page.run.assert_called_once_with("check")
                self.assertIn("最新版本（缓存）", page.versions.get())

    def test_release_is_saved_before_install_failure_and_restored_on_restart(self):
        for key in ("cli", "plus"):
            with self.subTest(project=key):
                page = self.page(key)
                target = str(page.target()).lower()
                page.events.put(("versions", (page.generation, target, "1.0.0"), "v1.0.2"))
                page.poll()
                self.assertEqual(self.app.profiles[key]["latest"][target], "v1.0.2")
                self.assertTrue(self.app.save.called)
                page.events.put(("error", None, "Installation failed"))
                with patch("cpa_manager.ui.pages.project.messagebox.showerror"):
                    page.poll()
                restored = self.page(key)
                self.assertEqual(restored.remote_version, "v1.0.2")
                self.assertIn("缓存", restored.remote_label())
                self.assertNotIn("缓存", page.remote_label())

    def test_old_directory_result_updates_its_cache_without_replacing_current_display(self):
        page = self.page("cli")
        old_target, generation = str(page.target()).lower(), page.generation
        page.directory.set(str(Path.cwd() / "another-project"))
        page.events.put(("versions", (generation, old_target, "1.0.0"), "v1.0.2"))
        page.poll()
        self.assertEqual(self.app.profiles["cli"]["latest"][old_target], "v1.0.2")
        self.assertIsNone(page.remote_version)
        self.assertIsNone(page.local_version)

    def test_confirmed_older_release_does_not_get_replaced_with_local_version(self):
        page = self.page("cli")
        page.remote_version, page.local_version = "v1.0.0", "1.0.2"
        page.refresh_stale_cache()
        page.events.put(("versions", (page.generation, str(page.target()).lower(), "1.0.2"), "v1.0.0"))
        page.poll()
        page.refresh_stale_cache()
        page.run.assert_called_once_with("check")
        self.assertEqual(page.remote_version, "v1.0.0")

    def test_equal_or_newer_release_does_not_trigger_extra_requests(self):
        page = self.page("plus")
        for remote in ("v1.0.0", "v1.1.0"):
            page.local_version, page.remote_version = "1.0.0", remote
            page.refresh_stale_cache()
        page.run.assert_not_called()

    def test_backup_cleanup_starts_local_task_only_after_confirmation(self):
        for key in ("cli", "plus"):
            with self.subTest(project=key):
                page = self.page(key)
                with patch("cpa_manager.ui.pages.project.backup_directories", return_value=[Path("backup")]), \
                     patch("cpa_manager.ui.pages.project.messagebox.askyesno", return_value=False):
                    page.clear_backups()
                page.run.assert_not_called()
                with patch("cpa_manager.ui.pages.project.backup_directories", return_value=[Path("backup")]), \
                     patch("cpa_manager.ui.pages.project.messagebox.askyesno", return_value=True):
                    page.clear_backups()
                page.run.assert_called_once_with("clear_backups")
                page.run.reset_mock()
                page.busy = True
                page.clear_backups()
                page.run.assert_not_called()
