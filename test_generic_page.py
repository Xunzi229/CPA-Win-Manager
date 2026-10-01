from pathlib import Path
from types import SimpleNamespace
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from generic_page import GenericPage


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


if __name__ == "__main__":
    unittest.main()
