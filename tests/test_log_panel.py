"""Tests for the shared Windows bottom log panel, clear-log action, and height resizing."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from cpa_manager.app import App
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.settings_store import read_settings
from cpa_manager.ui.pages.installer import InstallerPage
from cpa_manager.ui.pages.portable import PortablePage
from cpa_manager.ui.pages.project import ProjectPage
from cpa_manager.ui.widgets.log_panel import DEFAULT_LOG_LINES, EXPANDED_LOG_LINES, LOG_LINE_PIXELS, LogPanel


class LogPanelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root_dir = Path(self.directory.name).resolve()
        self.window = tk.Tk()
        self.window.withdraw()

    def tearDown(self):
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()

    def test_all_windows_pages_share_single_bottom_log_and_clear_together(self):
        settings_file = self.root_dir / "settings.json"
        app = SimpleNamespace(
            window=self.window,
            profiles={
                "cli": {"directory": str(self.root_dir / "cli"), "checks": {}, "latest": {}},
                "plus": {"directory": str(self.root_dir / "plus"), "checks": {}, "latest": {}},
            },
            proxy_settings={"enabled": False, "url": ""},
            proxy_status=Mock(),
            custom_profiles=[],
            installer_profiles=[],
            installer_download_directory=str(self.root_dir / "downloads"),
            release_cache=ReleaseCache(),
            settings_file=settings_file,
            download_workers=4,
            table_height_portable=7,
            table_height_installer=9,
            table_row_height=32,
            log_height=DEFAULT_LOG_LINES,
            manager_busy=False,
        )
        app.save = lambda: App.save(app)
        app.set_log_height = lambda lines: App.set_log_height(app, lines)
        app.clear_log = lambda: App.clear_log(app)

        app.log_panel = LogPanel(self.window, save_height=app.set_log_height, initial_height=app.log_height)
        app.log = app.log_panel.text
        notebook = ttk.Notebook(self.window)

        cli_page = ProjectPage(app, notebook, "cli", smoke=True)
        plus_page = ProjectPage(app, notebook, "plus", smoke=True)
        portable_page = PortablePage(app, notebook, self.root_dir)
        installer_page = InstallerPage(app, notebook)

        self.assertIs(cli_page.log, app.log)
        self.assertIs(plus_page.log, app.log)
        self.assertIs(portable_page.log, app.log)
        self.assertIs(installer_page.log, app.log)

        content = app.log.get("1.0", "end")
        self.assertIn("[CLIProxyAPI]", content)
        self.assertIn("[CPA-Manager-Plus]", content)
        self.assertIn("[免安装软件]", content)
        self.assertIn("[安装向导软件]", content)

        app.log_panel.clear_button.invoke()
        self.assertEqual(app.log.get("1.0", "end").strip(), "")

        portable_page.write("便携软件测试日志")
        installer_page.write("安装向导测试日志")
        content = app.log.get("1.0", "end")
        self.assertIn("便携软件测试日志", content)
        self.assertIn("安装向导测试日志", content)

    def test_log_panel_drag_double_click_and_persist_height(self):
        settings_file = self.root_dir / "settings.json"
        app = SimpleNamespace(
            profiles={},
            proxy_settings={"enabled": False, "url": ""},
            custom_profiles=[],
            installer_profiles=[],
            installer_download_directory=str(self.root_dir),
            release_cache=ReleaseCache(),
            settings_file=settings_file,
            download_workers=4,
            table_height_portable=7,
            table_height_installer=9,
            table_row_height=32,
            log_height=DEFAULT_LOG_LINES,
        )
        app.save = lambda: App.save(app)
        app.set_log_height = lambda lines: App.set_log_height(app, lines)
        panel = LogPanel(self.window, save_height=app.set_log_height, initial_height=app.log_height)
        app.log_panel = panel

        # Drag upward by 4 lines -> height increases by 4
        panel.on_bar_press(SimpleNamespace(y_root=500))
        panel.on_bar_drag(SimpleNamespace(y_root=500 - 4 * LOG_LINE_PIXELS))
        panel.on_bar_release()
        self.assertEqual(panel.current_lines(), DEFAULT_LOG_LINES + 4)
        self.assertEqual(read_settings(settings_file)["log_height"], DEFAULT_LOG_LINES + 4)

        # Double-click resets back to default when non-default, then expands on second double-click
        panel.on_bar_double_click()
        self.assertEqual(panel.current_lines(), DEFAULT_LOG_LINES)
        panel.on_bar_double_click()
        self.assertEqual(panel.current_lines(), EXPANDED_LOG_LINES)
        self.assertEqual(read_settings(settings_file)["log_height"], EXPANDED_LOG_LINES)

    def test_installed_rows_show_reinstall_label_while_keeping_muted_color(self):
        app = SimpleNamespace(
            window=self.window,
            custom_profiles=[
                {"id": "p_new", "name": "new", "directory": str(self.root_dir / "new"), "repository": "https://github.com/o/new", "mode": "便携安装", "selected_version": "v1.0.0"},
                {"id": "p_cur", "name": "cur", "directory": str(self.root_dir / "cur"), "repository": "https://github.com/o/cur", "mode": "便携安装", "local_version": "v1.0.0", "selected_version": "v1.0.0"},
                {"id": "p_old", "name": "old", "directory": str(self.root_dir / "old"), "repository": "https://github.com/o/old", "mode": "便携安装", "local_version": "v0.9.0", "selected_version": "v1.0.0"},
            ],
            installer_profiles=[
                {"id": "i_new", "name": "sparkle", "repository": "https://github.com/xishang0128/sparkle", "release": {"tag": "1.26.10", "assets": []}, "history": []},
                {"id": "i_cur", "name": "clash-party", "repository": "https://github.com/mihomo-party-org/clash-party", "release": {"tag": "v2.0.4", "assets": []}, "history": []},
                {"id": "i_old", "name": "clash-verge-rev", "repository": "https://github.com/clash-verge-rev/clash-verge-rev", "release": {"tag": "v2.5.9", "assets": []}, "history": []},
            ],
            installer_download_directory=str(self.root_dir / "dl"),
            manager_busy=False,
            save=Mock(),
        )
        notebook = ttk.Notebook(self.window)
        portable_page = PortablePage(app, notebook, self.root_dir)
        installer_page = InstallerPage(app, notebook)
        installer_page.installed_records = [
            {"id": "r1", "name": "clash-party", "version": "2.0.4"},
            {"id": "r2", "name": "clash-verge-rev", "version": "2.5.8"},
        ]

        # InstallerPage states: uninstalled -> 安装 (blue, not muted); current -> 重装 (gray, muted); outdated -> 升级 (blue, not muted)
        self.assertEqual(installer_page.action_label("i_new", "install", "安装"), "安装")
        self.assertFalse(installer_page.action_muted("i_new", "install"))
        self.assertEqual(installer_page.action_label("i_cur", "install", "安装"), "重装")
        self.assertTrue(installer_page.action_muted("i_cur", "install"))
        self.assertEqual(installer_page.action_label("i_old", "install", "安装"), "升级")
        self.assertFalse(installer_page.action_muted("i_old", "install"))

        # PortablePage states: uninstalled -> 安装 (blue, not muted); current -> 重装 (gray, muted); outdated -> 升级 (blue, not muted)
        self.assertEqual(portable_page.action_label("p_new", "install", "安装 / 更新"), "安装")
        self.assertFalse(portable_page.action_muted("p_new", "install"))
        self.assertEqual(portable_page.action_label("p_cur", "install", "安装 / 更新"), "重装")
        self.assertTrue(portable_page.action_muted("p_cur", "install"))
        self.assertEqual(portable_page.action_label("p_old", "install", "安装 / 更新"), "升级")
        self.assertFalse(portable_page.action_muted("p_old", "install"))
