import copy
import gc
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
        scan_patch = patch("cpa_manager.ui.pages.installer.scan_installed", return_value=[])
        scan_patch.start()
        self.addCleanup(scan_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = SimpleNamespace(window=self.window, installer_profiles=[],
            installer_download_directory=str(self.root), save=Mock(), proxy_url=lambda: "", manager_busy=False)
        self.page = InstallerPage(self.app, ttk.Notebook(self.window))
        self.page.run = Mock()

    def tearDown(self):
        for child in list(self.window.children.values()):
            if isinstance(child, tk.Toplevel):
                child.destroy()
        for timer in self.window.tk.call("after", "info"):
            self.window.after_cancel(timer)
        self.window.destroy()
        # Collect destroyed Tk objects on their owning thread before another worker starts.
        del self.page, self.app, self.window
        gc.collect()

    def test_add_same_repository_does_not_duplicate_row(self):
        self.page.repository.set("https://github.com/owner/tool")
        self.page.add()
        self.page.repository.set("https://github.com/OWNER/Tool/releases/latest")
        self.page.add()
        self.assertEqual(len(self.app.installer_profiles), 1)
        self.assertEqual(len(self.page.table.get_children()), 1)
        self.page.run.assert_called_with("check")

    def test_library_add_saves_named_row_without_network_and_hides_duplicate_action(self):
        self.window.geometry("1160x720")
        self.window.deiconify()
        self.page.open_software_library()
        library = self.page.software_library
        self.window.update()
        self.assertTrue(library.add_button.winfo_viewable())
        self.assertLess(library.add_button.winfo_rooty() + library.add_button.winfo_height(),
                        library.window.winfo_rooty() + library.window.winfo_height())
        entry = next(entry for entry in library.entries if entry["name"] == "Rufus")
        library.invoke(entry["id"], "add")
        self.assertEqual(len(self.app.installer_profiles), 1)
        self.assertEqual(self.app.installer_profiles[0]["name"], "Rufus")
        self.assertEqual(self.app.installer_profiles[0]["repository"], entry["repository"])
        self.app.save.assert_called_once()
        self.page.run.assert_not_called()
        self.assertIn("已添加", library.table.set(entry["id"], "name"))
        self.assertEqual(library.actions.tree.set(entry["id"], "add"), "")
        library.invoke(entry["id"], "add")
        self.assertEqual(len(self.app.installer_profiles), 1)
        self.app.save.assert_called_once()
        self.page.open_software_library()
        self.assertIs(self.page.software_library, library)

    def test_library_filter_and_unsupported_sources_disable_addition(self):
        self.page.open_software_library()
        library = self.page.software_library
        library.query.set("Forgejo")
        rows = library.table.get_children()
        self.assertEqual(len(rows), 1)
        library.table.selection_set(rows[0])
        library.selected()
        self.assertEqual(str(library.add_button["state"]), "disabled")
        library.invoke(rows[0], "add")
        self.assertFalse(self.app.installer_profiles)
        self.app.save.assert_not_called()

    def test_library_failed_save_rolls_back_main_row_and_can_retry(self):
        self.page.open_software_library()
        library = self.page.software_library
        entry = next(entry for entry in library.entries if entry["name"] == "Rufus")
        self.app.save.side_effect = OSError("locked")
        library.invoke(entry["id"], "add")
        self.assertFalse(self.app.installer_profiles)
        self.assertFalse(self.page.table.get_children())
        self.assertFalse(self.page.row_actions.tree.get_children())
        self.assertEqual(library.actions.tree.set(entry["id"], "add"), "添加")
        self.app.save.side_effect = None
        library.invoke(entry["id"], "add")
        self.assertEqual(len(self.app.installer_profiles), 1)

    def test_library_close_cancels_pending_action_render_and_can_reopen(self):
        self.page.open_software_library()
        library = self.page.software_library
        library.actions.schedule_render()
        timer = library.actions.render_timer
        self.assertIsNotNone(timer)
        library.window.destroy()
        self.assertNotIn(timer, self.window.tk.call("after", "info"))
        self.page.open_software_library()
        self.assertIsNot(self.page.software_library, library)
        self.assertTrue(self.page.software_library.window.winfo_exists())

    def test_catalog_add_keeps_existing_custom_name_and_obeys_busy_state(self):
        self.assertTrue(self.page.add("https://github.com/owner/tool", "自定义软件", check=False))
        self.assertTrue(self.page.add("https://github.com/OWNER/Tool", "软件库名称", check=False))
        self.assertEqual(self.app.installer_profiles[0]["name"], "自定义软件")
        self.page.busy = True
        self.assertFalse(self.page.add("https://github.com/other/tool", "其他软件", check=False))
        self.assertEqual(len(self.app.installer_profiles), 1)

    def test_library_github_search_is_async_and_result_can_be_added(self):
        import threading
        entered, finish = threading.Event(), threading.Event()
        self.page.open_software_library()
        library = self.page.software_library
        result = {"id": "github:remote/tool", "name": "remote/tool", "description": "远程软件",
                  "repository": "https://github.com/remote/tool", "categories": ["GitHub"]}
        def search(query, proxy):
            entered.set()
            finish.wait(5)
            return [result], 1
        with patch("cpa_manager.ui.widgets.software_library.search_repositories", side_effect=search) as fetch:
            try:
                library.query.set("tool")
                fetch.assert_not_called()
                library.github_search.set(True)
                library.search_changed(immediate=True)
                self.assertTrue(entered.wait(5))
                self.assertTrue(library.search_running)
                self.assertEqual(str(library.category_selector["state"]), "disabled")
            finally:
                finish.set()
            limit = time.monotonic() + 5
            while library.search_running and time.monotonic() < limit:
                self.window.update()
                time.sleep(0.01)
        self.assertEqual(library.table.get_children(), (result["id"],))
        library.invoke(result["id"], "add")
        self.assertEqual(self.app.installer_profiles[0]["repository"], result["repository"])
        self.page.run.assert_not_called()

    def test_library_switch_to_local_search_ignores_inflight_github_result(self):
        import threading
        entered, finish = threading.Event(), threading.Event()
        self.page.open_software_library()
        library = self.page.software_library
        def search(*_):
            entered.set()
            finish.wait(5)
            return [], 0
        with patch("cpa_manager.ui.widgets.software_library.search_repositories", side_effect=search):
            try:
                library.query.set("Rufus")
                library.github_search.set(True)
                library.search_changed(immediate=True)
                self.assertTrue(entered.wait(5))
                library.github_search.set(False)
                library.search_changed()
            finally:
                finish.set()
            limit = time.monotonic() + 5
            while library.search_running and time.monotonic() < limit:
                self.window.update()
                time.sleep(0.01)
        rows = library.table.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(library.table.set(rows[0], "name"), "Rufus")
        self.assertEqual(str(library.category_selector["state"]), "readonly")

    def test_installed_version_refresh_and_manual_association_survive_download_snapshot(self):
        p = profile()
        self.app.installer_profiles.append(p)
        record = {"id": "registry-record", "name": p["name"], "version": "1.2.0", "directory": str(self.root)}
        self.page.events.put(("installed", [record]))
        self.page.poll()
        self.assertEqual(p["installed_id"], record["id"])
        self.assertEqual(self.page.table.set(p["id"], "local"), "1.2.0")
        self.assertEqual(self.page.installed_records[0]["directory"], str(self.root))
        snapshot = dict(p)
        snapshot.pop("installed_id")
        self.page.replace_profile(snapshot)
        self.assertEqual(self.app.installer_profiles[0]["installed_id"], record["id"])
        self.page.events.put(("installed", [dict(record, version="1.3.0")]))
        self.page.poll()
        self.assertEqual(self.page.table.set(p["id"], "local"), "1.3.0")
        self.page.events.put(("installed", []))
        self.page.poll()
        self.assertEqual(self.page.table.set(p["id"], "local"), "未检测到")

    def test_unlink_persists_and_does_not_automatically_reassociate(self):
        p = profile()
        self.app.installer_profiles.append(p)
        record = {"id": "fixture", "name": p["name"], "version": "1", "directory": str(self.root)}
        self.page.events.put(("installed", [record]))
        self.page.poll()
        self.page.table.selection_set(p["id"])
        self.page.unbind_installed()
        self.assertEqual(p["installed_id"], "")
        self.assertFalse(p["installed_auto"])
        restored = backend.load_profiles([p])[0]
        self.assertFalse(restored["installed_auto"])
        self.page.events.put(("installed", [record]))
        self.page.poll()
        self.assertEqual(self.page.table.set(p["id"], "local"), "未关联")
        self.assertFalse(self.page.row_action_visible(p["id"], "uninstall"))
        self.assertTrue(self.page.bind_installed(p, record))
        self.assertTrue(self.page.row_action_visible(p["id"], "uninstall"))

    def test_failed_unlink_keeps_association(self):
        p = profile()
        p["installed_id"] = "previous"
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        self.page.table.selection_set(p["id"])
        self.app.save.side_effect = OSError("locked")
        self.page.unbind_installed()
        self.assertEqual(p["installed_id"], "previous")
        self.assertNotIn("installed_auto", p)

    def test_failed_association_save_keeps_previous_binding(self):
        p = profile()
        p["installed_id"] = "previous"
        self.app.installer_profiles.append(p)
        self.app.save.side_effect = OSError("locked")
        self.assertFalse(self.page.bind_installed(p, {"id": "new", "name": "New"}))
        self.assertEqual(p["installed_id"], "previous")

    def test_installed_update_dot_changes_with_current_system_version(self):
        p = profile()
        p["release"]["tag"] = "v2.0.0"
        self.app.installer_profiles.append(p)
        record = {"id": "fixture", "name": p["name"], "version": "1.0.0", "directory": str(self.root)}
        self.page.events.put(("installed", [record]))
        self.page.poll()
        self.assertIn(p["id"], self.page.update_badges.rows)
        self.page.frame.master.pack(fill="both", expand=True)
        self.page.frame.pack(fill="both", expand=True)
        self.window.geometry("1100x500")
        self.window.deiconify()
        self.window.update()
        self.page.table.see(p["id"])
        self.page.table.xview_moveto(1)
        self.window.update()
        self.assertIn(p["id"], self.page.update_badges.labels)
        self.assertEqual(self.page.update_badges.icon.get(4, 4), (229, 57, 53))
        self.page.events.put(("installed", [dict(record, version="2.0.0")]))
        self.page.poll()
        self.window.update()
        self.assertNotIn(p["id"], self.page.update_badges.rows)
        self.assertNotIn(p["id"], self.page.update_badges.labels)
        self.page.events.put(("installed", []))
        self.page.poll()
        self.assertNotIn(p["id"], self.page.update_badges.rows)

    def test_all_installer_columns_are_centered(self):
        self.assertNotIn("downloaded", self.page.table["columns"])
        self.assertNotIn("directory", self.page.table["columns"])
        self.assertNotIn("state", self.page.table["columns"])
        self.assertEqual([action[0] for action in self.page.row_actions.actions], ["check", "install", "uninstall"])
        for column in self.page.table["columns"]:
            self.assertEqual(str(self.page.table.column(column, "anchor")), "center")
            self.assertEqual(str(self.page.table.heading(column, "anchor")), "center")

    def test_uninstall_button_hidden_until_associated_and_removed_when_record_disappears(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        self.page.frame.master.pack(fill="both", expand=True)
        self.page.frame.pack(fill="both", expand=True)
        self.window.geometry("1100x500")
        self.window.deiconify()
        self.window.update()
        self.assertNotIn((p["id"], "uninstall"), self.page.row_actions.buttons)
        self.assertEqual(self.page.row_actions.tree.set(p["id"], "uninstall"), "")
        self.page.row_actions.activate(p["id"], "uninstall")
        self.page.run.assert_not_called()
        record = {"id": "fixture", "name": p["name"], "version": "1", "directory": str(self.root)}
        self.page.events.put(("installed", [record]))
        self.page.poll()
        self.window.update()
        self.assertIn((p["id"], "uninstall"), self.page.row_actions.buttons)
        self.assertEqual(self.page.row_actions.tree.set(p["id"], "uninstall"), "卸载")
        self.page.events.put(("installed", []))
        self.page.poll()
        self.window.update()
        self.assertNotIn((p["id"], "uninstall"), self.page.row_actions.buttons)

    def test_uninstall_requires_association_and_confirmation_and_never_downloads(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        self.page.table.selection_set(p["id"])
        with patch("cpa_manager.ui.pages.installer.launch_uninstaller") as launch, \
             patch("cpa_manager.ui.pages.installer.messagebox.askyesno", return_value=False) as confirm, \
             patch.object(backend, "prepare") as download:
            self.page.uninstall_selected()
            launch.assert_not_called()
            confirm.assert_not_called()
            record = {"id": "fixture", "name": p["name"], "version": "1", "directory": str(self.root)}
            self.page.installed_records = [record]
            self.page.uninstall_selected()
            launch.assert_not_called()
            confirm.return_value = True
            InstallerPage.run(self.page, "uninstall")
            launch.assert_called_once_with(record)
            download.assert_not_called()

    def test_installation_directory_remains_available_from_context_menu(self):
        p = profile()
        self.app.installer_profiles.append(p)
        self.page.update_row(p)
        self.page.table.selection_set(p["id"])
        self.page.installed_records = [{"id": "fixture", "name": p["name"], "version": "1", "directory": str(self.root)}]
        self.page.copy_installed_folder()
        self.assertEqual(self.window.clipboard_get(), str(self.root))
        with patch("cpa_manager.ui.pages.installer.os.startfile") as open_folder:
            self.page.open_installed_folder()
            open_folder.assert_called_once_with(str(self.root))

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

    def test_fixed_row_action_targets_clicked_row_and_blocks_while_busy(self):
        for identifier in ("first", "second"):
            p = profile()
            p["id"] = identifier
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        self.page.table.selection_set("first")
        tree = self.page.row_actions.tree
        event = SimpleNamespace(x=10, y=10)
        with patch.object(tree, "identify_region", return_value="cell"), \
             patch.object(tree, "identify_row", return_value="second"), \
             patch.object(tree, "identify_column", return_value="#1"):
            self.page.row_actions.clicked(event)
            self.page.run.assert_called_once_with("check")
            self.assertEqual(self.page.profile()["id"], "second")
            self.page.run.reset_mock()
            self.page.set_busy(True)
            self.page.row_actions.clicked(event)
            self.page.run.assert_not_called()
        self.page.set_busy(False)
        self.window.update()
        self.page.table.selection_set("first")
        self.window.update()
        self.assertEqual(tree.selection(), ("first",))

    def test_fixed_actions_do_not_move_on_horizontal_scroll_and_sync_vertical_scroll(self):
        for index in range(30):
            p = profile()
            p["id"] = str(index)
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        self.page.frame.master.pack(fill="both", expand=True)
        self.page.frame.pack(fill="both", expand=True)
        self.window.geometry("800x500")
        self.window.deiconify()
        self.window.update()
        tree = self.page.row_actions.tree
        position = (tree.winfo_x(), tree.winfo_width())
        button = self.page.row_actions.buttons[("0", "install")]
        button.invoke()
        self.page.run.assert_called_once_with("install")
        self.assertEqual(self.page.profile()["id"], "0")
        self.page.run.reset_mock()
        self.page.set_busy(True)
        self.window.update()
        button.invoke()
        self.page.run.assert_not_called()
        self.page.set_busy(False)
        self.window.update()
        self.page.table.xview_moveto(1)
        self.window.update()
        self.assertGreater(self.page.table.xview()[0], 0)
        self.assertEqual((tree.winfo_x(), tree.winfo_width()), position)
        self.assertEqual(tree.xview()[0], 0)
        self.page.table.yview_moveto(0.5)
        self.window.update()
        self.assertAlmostEqual(self.page.table.yview()[0], tree.yview()[0], places=5)
        tree.yview_moveto(0.2)
        self.window.update()
        self.assertAlmostEqual(self.page.table.yview()[0], tree.yview()[0], places=5)
        self.page.table.selection_set("0")
        with patch("cpa_manager.ui.pages.installer.messagebox.askyesno", return_value=True):
            self.page.remove()
        self.assertFalse(tree.exists("0"))

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
                while self.page.pending_downloads and time.monotonic() < limit:
                    self.page.poll()
                    time.sleep(0.01)
                self.assertFalse(self.page.pending_downloads)
                self.assertEqual(downloading.call_count, 2)
                launching.assert_not_called()

    def test_install_reuses_verified_local_package_or_downloads_before_launch(self):
        for cached in (True, False):
            with self.subTest(cached=cached), tempfile.TemporaryDirectory() as directory:
                p = profile()
                p["id"] = "cached" if cached else "missing"
                root = Path(directory).resolve()
                folder = root / backend.folder_name(p)
                folder.mkdir()
                package = folder / "installer-fixture-setup.exe"
                if cached:
                    package.write_bytes(b"verified package")
                    asset = p["release"]["assets"][0]
                    p["history"] = [{"version": p["release"]["tag"], "asset": asset["name"],
                                     "url": asset["url"], "identity": backend.github.asset_identity(asset),
                                     "path": str(package), "sha256": backend.file_digest(package),
                                     "directory": str(folder), "repository": p["repository"]}]
                self.app.installer_profiles.append(p)
                self.page.update_row(p)
                self.page.table.selection_set(p["id"])
                self.app.installer_download_directory = str(root)
                def download(*args):
                    package.write_bytes(b"downloaded package")
                    return package
                with patch.object(backend, "refresh", side_effect=AssertionError("安装已有版本不需要联网检查")), \
                     patch.object(backend, "download_installer", side_effect=download) as downloading, \
                     patch("cpa_manager.ui.pages.installer.os.startfile") as launching:
                    InstallerPage.run(self.page, "install")
                    limit = time.monotonic() + 5
                    while self.page.pending_downloads and time.monotonic() < limit:
                        self.page.poll()
                        time.sleep(0.01)
                    self.assertFalse(self.page.pending_downloads)
                    self.assertEqual(downloading.call_count, 0 if cached else 1)
                    launching.assert_called_once_with(package)

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
                while self.page.pending_downloads and time.monotonic() < limit:
                    self.page.poll()
                    time.sleep(0.01)
                self.assertFalse(self.page.pending_downloads)
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
            while self.page.pending_downloads and time.monotonic() < limit:
                self.page.poll()
                time.sleep(0.01)
            self.assertFalse(self.page.pending_downloads)
            self.assertEqual(downloading.call_count, 1)
            launching.assert_not_called()

    def test_row_check_is_async_and_other_rows_remain_editable(self):
        import threading
        entered, finish = threading.Event(), threading.Event()
        for identity in ("first", "second"):
            p = profile()
            p.update(id=identity, repository=f"https://github.com/owner/{identity}")
            self.app.installer_profiles.append(p)
            self.page.update_row(p)
        def refresh(p, proxy):
            entered.set()
            finish.wait(5)
            p["release"]["tag"] = "v2"
            return p
        self.page.table.selection_set("first")
        with patch.object(backend, "refresh", side_effect=refresh) as fetch:
            try:
                InstallerPage.run(self.page, "check")
                self.assertTrue(entered.wait(5))
                self.assertFalse(self.page.busy)
                self.assertTrue(self.page.checking)
                self.assertFalse(self.page.row_actions.enabled("first", "check"))
                self.assertTrue(self.page.row_actions.enabled("second", "check"))
                self.assertEqual(self.page.address_value("second", "repository"), "https://github.com/owner/second")
                InstallerPage.run(self.page, "check")
            finally:
                finish.set()
            limit = time.monotonic() + 5
            while self.page.checking and time.monotonic() < limit:
                self.page.poll()
                time.sleep(0.01)
        self.assertFalse(self.page.checking)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.page.table.set("first", "latest"), "v2")
        self.assertEqual(self.page.table.set("second", "latest"), "v2")


if __name__ == "__main__":
    unittest.main()
