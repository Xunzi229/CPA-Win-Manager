import copy
import hashlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from cpa_manager.backends import installer as backend
from cpa_manager.ui.pages.installer import InstallerPage


def profile():
    asset = {"name": "tool-windows-amd64-setup.exe", "size": 3,
             "url": "https://github.com/owner/tool/releases/download/v1/setup.exe", "digest": "sha256:test"}
    return {"id": "fixture", "name": "Tool", "repository": "https://github.com/owner/tool",
            "selected_asset": asset["name"], "release": {"tag": "v1", "assets": [asset]}, "history": []}


class InstallerTests(unittest.TestCase):
    def test_repository_uniqueness_and_legacy_migration(self):
        rows = backend.load_profiles([{"repository": "https://github.com/OWNER/Tool/releases/latest", "name": "first"}],
            [{"repository": "https://github.com/owner/tool.git", "name": "duplicate"},
             {"repository": "https://github.com/other/app", "name": "legacy"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "first")
        self.assertEqual(rows[1]["name"], "legacy")

    def test_same_version_reuses_download_new_version_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def download(release, asset, folder, proxy, report, cancel):
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"installer-{len(calls)}-setup.exe"
                path.write_bytes(b"new")
                calls.append(path)
                return path
            with patch.object(backend, "download_installer", side_effect=download):
                first, path = backend.prepare(profile(), directory, "", lambda *_: None)
                second, reused = backend.prepare(first, directory, "", lambda *_: None)
                self.assertEqual(path, reused)
                self.assertEqual(len(calls), 1)
                second["release"]["tag"] = "v2"
                third, newer = backend.prepare(second, directory, "", lambda *_: None)
                self.assertNotEqual(path, newer)
                self.assertEqual(len(calls), 2)
                self.assertEqual(backend.downloaded_version(third), "v2")

    def test_corrupt_file_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            p = profile()
            folder = Path(directory) / backend.folder_name(p)
            folder.mkdir()
            file = folder / "installer-old-setup.exe"
            file.write_bytes(b"bad")
            p["history"] = [{"version": "v1", "asset": p["selected_asset"], "url": p["release"]["assets"][0]["url"],
                "identity": backend.github.asset_identity(p["release"]["assets"][0]), "path": str(file),
                "sha256": hashlib.sha256(b"good").hexdigest()}]
            self.assertIsNone(backend.cached_download(p, verify=True))

    def test_clear_deletes_only_recorded_owned_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            p = profile()
            folder = Path(directory) / backend.folder_name(p)
            folder.mkdir()
            owned = folder / "installer-old-setup.exe"
            owned.write_bytes(b"old")
            other = Path(directory) / "unrelated.exe"
            other.write_bytes(b"keep")
            p["history"] = [{"version": "v1", "path": str(owned), "directory": str(folder)},
                            {"version": "v0", "path": str(other), "directory": directory}]
            with patch.object(backend.github.transfer, "discard"):
                updated, errors = backend.clear_history(p)
            self.assertFalse(owned.exists())
            self.assertTrue(other.exists())
            self.assertEqual(len(updated["history"]), 1)
            self.assertTrue(errors)

    def test_refresh_keeps_selected_asset_or_recommends_native_installer(self):
        p = profile()
        release = copy.deepcopy(p["release"])
        release["assets"].append({"name": "tool-windows-arm64-setup.exe", "url": "https://example.test/arm.exe"})
        with patch.object(backend.github, "releases", return_value=release), \
             patch.object(backend.github, "windows_architecture", return_value="amd64"):
            updated = backend.refresh(p, "")
        self.assertEqual(updated["selected_asset"], p["selected_asset"])


class InstallerPageTests(unittest.TestCase):
    def setUp(self):
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = SimpleNamespace(window=self.window, installer_profiles=[],
            installer_download_directory=str(Path.cwd()), save=Mock(), proxy_url=lambda: "", manager_busy=False)
        self.page = InstallerPage(self.app, ttk.Notebook(self.window))
        self.page.run = Mock()

    def tearDown(self):
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()

    def test_add_same_repository_does_not_duplicate_row(self):
        self.page.repository.set("https://github.com/owner/tool")
        self.page.add()
        self.page.repository.set("https://github.com/OWNER/Tool/releases/latest")
        self.page.add()
        self.assertEqual(len(self.app.installer_profiles), 1)
        self.assertEqual(len(self.page.table.get_children()), 1)
        self.page.run.assert_called_with("check")

    def test_all_installer_columns_are_centered(self):
        for column in self.page.table["columns"]:
            self.assertEqual(str(self.page.table.column(column, "anchor")), "center")
            self.assertEqual(str(self.page.table.heading(column, "anchor")), "center")

    def test_inline_package_selection_updates_the_correct_row(self):
        p = profile()
        p["release"]["assets"].append({"name": "other-x86.exe", "size": 1024})
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        values, _ = self.page.inline_choices(p["id"], "package")
        choice = next(v for v in values if "other-x86" in v)
        self.page.inline_commit(p["id"], "package", choice)
        self.assertEqual(p["selected_asset"], "other-x86.exe")
        self.assertIn("other-x86.exe", self.page.table.set(p["id"], "package"))
        self.assertEqual(self.page.table.set(p["id"], "package"), "other-x86.exe")
        self.assertEqual(self.page.table.set(p["id"], "size"), "1.00 KB")
        self.app.save.assert_called()

    def test_inline_editor_survives_selection_event_and_commits_choice(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        event = SimpleNamespace(x=10, y=10)
        with patch.object(self.page.table, "identify_region", return_value="cell"), \
             patch.object(self.page.table, "identify_row", return_value=p["id"]), \
             patch.object(self.page.table, "identify_column", return_value="#3"), \
             patch.object(self.page.table, "bbox", return_value=(0, 0, 240, 24)):
            self.page.inline.open(event)
        self.window.update()
        self.assertIsNotNone(self.page.inline.editor)
        self.page.inline.editor.current(0)
        self.page.inline.editor.event_generate("<<ComboboxSelected>>")
        self.assertIsNone(self.page.inline.editor)
        self.assertEqual(p["selected_asset"], p["release"]["assets"][0]["name"])

    def test_context_menu_targets_right_clicked_row(self):
        for identifier in ("first", "second"):
            p = profile()
            p["id"] = identifier
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        self.page.table.selection_set("first")
        menu = Mock()
        event = SimpleNamespace(x=10, y=10, x_root=20, y_root=20)
        with patch.object(self.page.table, "identify_row", return_value="second"), \
             patch("cpa_manager.ui.pages.installer.tk.Menu", return_value=menu):
            self.page.context_menu(event)
        self.assertEqual(self.page.profile()["id"], "second")
        commands = {call.kwargs["label"]: call.kwargs["command"] for call in menu.add_command.call_args_list}
        commands["检查此行"]()
        self.page.run.assert_called_once_with("check")

    def test_inline_address_edit_preserves_cleanup_of_original_downloads(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / backend.folder_name(p)
            folder.mkdir()
            package = folder / "installer-old-setup.exe"
            package.write_bytes(b"old")
            p["history"] = [{"directory": str(folder), "path": str(package), "version": "v1"}]
            self.page.address_value(p["id"], "repository")
            self.assertFalse(self.page.address_commit(p["id"], "repository", "invalid"))
            self.assertTrue(self.page.address_commit(p["id"], "repository", "https://github.com/owner/new"))
            self.assertIsNone(p["release"])
            self.assertEqual(backend.downloaded_version(p), "—")
            with patch.object(backend.github.transfer, "discard"):
                updated, errors = backend.clear_history(p)
            self.assertFalse(package.exists())
            self.assertFalse(errors)
            self.assertEqual(updated["history"], [])

    def test_inline_address_entry_is_not_closed_on_double_click_release(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        event = SimpleNamespace(x=10, y=10)
        with patch.object(self.page.table, "identify_region", return_value="cell"), \
             patch.object(self.page.table, "identify_row", return_value=p["id"]), \
             patch.object(self.page.table, "identify_column", return_value="#2"), \
             patch.object(self.page.table, "bbox", return_value=(0, 0, 220, 24)):
            self.page.inline.edit_text(event)
            self.page.inline.open(event)
        self.assertIsInstance(self.page.inline.editor, ttk.Entry)
        self.assertEqual(self.page.inline.editor.get(), p["repository"])
        self.page.inline.close()

    def test_batch_download_skips_current_packages_and_does_not_launch_installers(self):
        rows = []
        for identifier in ("current", "old", "new"):
            p = profile()
            p["id"] = identifier
            rows.append(p)
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.exe"
            path.write_bytes(b"new")
            def refresh(p, _proxy):
                p["release"]["tag"] = "v2"
                return p
            def prepare(p, *_args):
                p["history"] = [{"path": str(path), "version": "v2"}]
                return p, path
            with patch.object(backend, "refresh", side_effect=refresh), \
                 patch.object(backend, "cached_download", side_effect=lambda p, **kw: path if p["id"] == "current" else None), \
                 patch.object(backend, "prepare", side_effect=prepare) as downloading, \
                 patch("cpa_manager.ui.pages.installer.os.startfile") as launching:
                InstallerPage.run(self.page, "download", all_rows=True)
                limit = time.monotonic() + 5
                while self.page.busy and time.monotonic() < limit:
                    self.page.poll()
                    time.sleep(0.01)
                self.assertFalse(self.page.busy)
                self.assertEqual(downloading.call_count, 2)
                launching.assert_not_called()

    def test_single_row_update_opens_installer_after_record_is_saved(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        self.page.table.selection_set(p["id"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.exe"
            path.write_bytes(b"new")
            with patch.object(backend, "refresh", return_value=p), \
                 patch.object(backend, "prepare", return_value=(p, path)), \
                 patch("cpa_manager.ui.pages.installer.os.startfile") as launching:
                InstallerPage.run(self.page, "update")
                limit = time.monotonic() + 5
                while self.page.busy and time.monotonic() < limit:
                    self.page.poll()
                    time.sleep(0.01)
                self.assertFalse(self.page.busy)
                launching.assert_called_once_with(path)
                self.assertTrue(self.app.save.called)

    def test_cancel_during_commit_stops_batch_and_suppresses_installer_launch(self):
        import threading
        entered, finish = threading.Event(), threading.Event()
        for identifier in ("first", "second"):
            p = profile()
            p["id"] = identifier
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        def prepare(p, directory, proxy, report, control):
            control.begin_commit()
            entered.set()
            if not finish.wait(5):
                raise TimeoutError("Test did not release commit")
            return p, Path("fixture.exe")
        with patch.object(backend, "refresh", side_effect=lambda p, proxy: p), \
             patch.object(backend, "prepare", side_effect=prepare) as downloading, \
             patch("cpa_manager.ui.pages.installer.os.startfile") as launching:
            try:
                InstallerPage.run(self.page, "update", all_rows=True)
                self.assertTrue(entered.wait(5))
                self.assertFalse(self.page.request_stop())
            finally:
                finish.set()
            limit = time.monotonic() + 5
            while self.page.busy and time.monotonic() < limit:
                self.page.poll()
                time.sleep(0.01)
            self.assertFalse(self.page.busy)
            self.assertEqual(downloading.call_count, 1)
            launching.assert_not_called()


if __name__ == "__main__":
    unittest.main()
