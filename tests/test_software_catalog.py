import unittest

from cpa_manager.core.software_catalog import catalog_entries, filter_entries, GROUPS
from cpa_manager.ui.widgets.software_library import SoftwareLibrary


class SoftwareCatalogTests(unittest.TestCase):
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
