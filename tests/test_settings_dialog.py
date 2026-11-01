"""Settings validation failures must leave a working way to close the dialog."""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from cpa_manager.app import App
from cpa_manager.backends.release_cache import ReleaseCache


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App.__new__(App)
        self.app.window = self.root
        self.app.proxy_dialog = None
        self.app.proxy_settings = {"enabled": False, "url": ""}
        self.app.proxy_status = Mock()
        self.app.installer_download_directory = self.directory.name
        self.app.download_workers = 4
        self.app.release_cache = ReleaseCache()
        self.app.save = Mock()
        self.app.manager_status = tk.StringVar(master=self.root, value="v1.5.20")
        self.app.refresh_manager_controls = Mock()
        self.app.open_proxy_settings()
        self.root.update_idletasks()

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def done_button(self):
        def find(widget):
            if isinstance(widget, ttk.Button) and widget.cget("text") == "完成":
                return widget
            for child in widget.winfo_children():
                found = find(child)
                if found is not None:
                    return found
        return find(self.app.proxy_dialog)

    def test_titlebar_close_works_after_failed_save(self):
        dialog = self.app.proxy_dialog
        self.app.save.side_effect = FileNotFoundError(2, "No such file or directory")
        self.done_button().invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertIsNotNone(self.app.proxy_dialog)
        self.root.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(dialog.winfo_exists())
        self.assertIsNone(self.app.proxy_dialog)
        self.assertIsNone(self.app.manager_check)

    def test_programmatic_close_discards_invalid_changes_and_cancels_autosave(self):
        dialog = self.app.proxy_dialog
        # Editing the download directory queues a delayed automatic save.
        entries = []
        def collect(widget):
            if isinstance(widget, ttk.Entry):
                entries.append(widget)
            for child in widget.winfo_children():
                collect(child)
        collect(dialog)
        entries[1].delete(0, "end")
        entries[1].insert(0, str(Path(self.directory.name) / "changed"))
        self.assertTrue(self.root.tk.call("after", "info"))
        self.app.save.side_effect = OSError("disk unavailable")
        self.assertTrue(self.app.close_proxy_dialog())
        self.assertEqual(self.app.installer_download_directory, self.directory.name)
        self.assertFalse(self.root.tk.call("after", "info"))

    def test_done_saves_and_closes_successfully(self):
        dialog = self.app.proxy_dialog
        self.done_button().invoke()
        self.app.save.assert_called_once()
        self.assertFalse(dialog.winfo_exists())
        self.assertIsNone(self.app.proxy_dialog)
