import unittest
import json
import urllib.parse
from unittest.mock import patch

from cpa_manager.core.software_catalog import catalog_entries, filter_entries, GROUPS
from cpa_manager.ui.widgets.software_library import SoftwareLibrary
from cpa_manager.backends import github


class SoftwareCatalogTests(unittest.TestCase):
    def test_github_search_requests_share_minimum_interval_even_after_failure(self):
        clock = [10.0]
        starts = []
        def sleep(seconds):
            clock[0] += seconds
        def read(*_):
            starts.append(clock[0])
            if len(starts) == 2:
                raise OSError("network error")
            return '{"items": [], "total_count": 0}'
        with patch.object(github, "_last_search", float("-inf")), \
             patch.object(github.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(github.time, "sleep", side_effect=sleep), \
             patch.object(github, "network"), patch.object(github, "read_text", side_effect=read):
            github.search_repositories("first")
            with self.assertRaises(OSError):
                github.search_repositories("second")
            github.search_repositories("third")
        self.assertEqual(starts, [10.0, 11.5, 13.0])

    def test_github_search_encodes_query_and_maps_unique_repository_results(self):
        payload = {"total_count": 3, "items": [{"full_name": "owner/工具", "description": "invalid"},
                   {"full_name": "owner/tool", "description": "说明"},
                   {"full_name": "OWNER/Tool", "description": "duplicate"},
                   {"full_name": "other/app", "description": None}]}
        with patch.object(github, "network") as network, patch.object(github, "read_text", return_value=json.dumps(payload)) as read:
            entries, total = github.search_repositories("终端 windows", "http://127.0.0.1:7890")
        network.assert_called_once_with("http://127.0.0.1:7890")
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[1]).query)
        self.assertEqual(params["q"], ["终端 windows"])
        self.assertEqual(params["per_page"], ["30"])
        self.assertEqual(total, 3)
        self.assertEqual([entry["repository"] for entry in entries], ["https://github.com/owner/tool", "https://github.com/other/app"])
        self.assertEqual(entries[1]["description"], "暂无项目说明")

    def test_catalog_has_unique_sources_and_required_details(self):
        entries = catalog_entries()
        self.assertGreater(len(entries), 100)
        self.assertEqual(len({entry["id"] for entry in entries}), len(entries))
        sources = [entry["repository"].casefold() for entry in entries if entry["repository"]]
        self.assertEqual(len(sources), len(set(sources)))
        for entry in entries:
            self.assertTrue(entry["name"])
            self.assertTrue(entry["description"])
            self.assertTrue(set(entry["categories"]) <= set(GROUPS))
            if entry["repository"].startswith("https://github.com/"):
                self.assertTrue(SoftwareLibrary.addable(entry))

    def test_duplicate_vaultwarden_is_available_in_both_categories(self):
        entries = catalog_entries()
        records = filter_entries(entries, query="Vaultwarden")
        self.assertEqual(len(records), 1)
        self.assertIn(records[0], filter_entries(entries, "密码 / 安全"))
        self.assertIn(records[0], filter_entries(entries, "自托管 / 私有服务"))

    def test_search_matches_description_address_and_category(self):
        entries = catalog_entries()
        self.assertEqual(filter_entries(entries, query="  WInDirStat ")[0]["name"], "WinDirStat")
        self.assertEqual(filter_entries(entries, query="pbatard/rufus")[0]["name"], "Rufus")
        self.assertTrue(filter_entries(entries, query="磁盘"))
        self.assertEqual(filter_entries(entries, query="不存在的软件"), [])
        self.assertEqual(filter_entries(entries, "密码 / 安全", "rufus"), [])

    def test_non_github_entries_remain_visible_without_add_action(self):
        entries = catalog_entries()
        self.assertNotIn("LM Studio", [entry["name"] for entry in entries])
        for name in ("Forgejo",):
            entry = next(entry for entry in entries if entry["name"] == name)
            self.assertFalse(SoftwareLibrary.addable(entry))
        bitwarden = next(entry for entry in entries if entry["name"] == "Bitwarden")
        self.assertEqual(bitwarden["repository"], "https://github.com/bitwarden/clients")

    def test_click_open_arrow_opens_browser(self):
        import tkinter as tk
        from types import SimpleNamespace
        root = tk.Tk()
        root.withdraw()
        try:
            library = SoftwareLibrary(root, lambda _: True, lambda _: False, lambda: True)
            first_row = library.table.get_children()[0]
            entry = library.by_id[first_row]

            with patch.object(library.table, "bbox", return_value=(100, 20, 450, 32)):
                library.render_link_arrows()
            arrow = library.link_arrows[first_row]
            self.assertEqual(arrow.cget("text"), "↗")
            self.assertEqual(str(arrow.cget("foreground")), "#2563eb")
            self.assertEqual(str(arrow.cget("cursor")), "hand2")

            with patch("cpa_manager.ui.widgets.software_library.webbrowser.open") as mock_open:
                library.open_repository_row(first_row)
                mock_open.assert_called_once_with(entry["repository"])

            library.link_arrow_enter(SimpleNamespace(widget=arrow))
            self.assertEqual(str(arrow.cget("foreground")), "#1d4ed8")
            library.link_arrow_leave(SimpleNamespace(widget=arrow))
            self.assertEqual(str(arrow.cget("foreground")), "#2563eb")

            library.window.destroy()
        finally:
            root.destroy()
