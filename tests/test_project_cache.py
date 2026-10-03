"""Project release cache survives installs and reconciles with newer binaries."""
from pathlib import Path
from types import SimpleNamespace
import time
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from cpa_manager.config import PROJECTS
from cpa_manager.ui.pages.project import ProjectPage


class ProjectCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        # Build explicit fixtures; never discover installed software on this machine.
        profiles = {key: {"directory": str(self.root / key), "checks": {}, "latest": {}}
                    for key in PROJECTS}
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = SimpleNamespace(window=self.window, profiles=profiles,
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
        page.directory.set(str(self.root / "another-project"))
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

    def test_upgrade_button_enabled_only_when_update_available(self):
        for key in ("cli", "plus"):
            with self.subTest(project=key):
                page = self.page(key)
                target = page.target()
                self.assertTrue(target.resolve().is_relative_to(self.root))
                target.mkdir(parents=True, exist_ok=True)
                exe = target / page.executable

                # Case 1: Not installed -> "安装最新版", enabled
                self.assertFalse(exe.exists())
                page.local_version, page.remote_version = None, "v1.0.0"
                page.button_states()
                self.assertEqual(page.install.cget("text"), "安装最新版")
                self.assertEqual(str(page.install.cget("state")), "normal")

                # Case 2: Installed, same version (no update) -> "升级", disabled
                exe.write_bytes(b"dummy")
                page.local_version, page.remote_version = "1.0.0", "v1.0.0"
                page.button_states()
                self.assertEqual(page.install.cget("text"), "升级")
                self.assertEqual(str(page.install.cget("state")), "disabled")

                # Case 3: Installed, newer version available -> "升级", normal (enabled)
                page.local_version, page.remote_version = "1.0.0", "v1.1.0"
                page.button_states()
                self.assertEqual(page.install.cget("text"), "升级")
                self.assertEqual(str(page.install.cget("state")), "normal")

                # TemporaryDirectory owns cleanup, including when an assertion fails.

    def test_upgrade_test_never_discovers_or_changes_sibling_installations(self):
        # Simulate real software next to a manager, but keep the regression fixture temporary.
        siblings = self.root / "user-software"
        originals = {}
        for key, (name, executable, _) in PROJECTS.items():
            directory = siblings / name
            directory.mkdir(parents=True)
            path = directory / executable
            path.write_bytes(b"original software executable")
            originals[path] = path.read_bytes()
        with patch("cpa_manager.config.ROOT", siblings / "manager"), \
             patch("cpa_manager.config.default_profiles", side_effect=AssertionError("禁止测试发现真实目录")):
            self.test_upgrade_button_enabled_only_when_update_available()
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
