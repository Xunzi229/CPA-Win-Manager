"""Sorting keeps pinned software and frozen row actions in sync."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from cpa_manager.backends.installer import load_profiles
from cpa_manager.ui.widgets.table_order import TableOrder, sort_value


class TableOrderTests(unittest.TestCase):
    def setUp(self):
        self.window = tk.Tk()
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.table = ttk.Treeview(self.window, columns=("name", "local", "size"), show="headings")
        for column in self.table["columns"]:
            self.table.heading(column, text=column)
        peer = ttk.Treeview(self.window)
        self.actions = Mock(tree=peer)
        self.profiles = [{"id": "a"}, {"id": "b"}, {"id": "c", "pinned": True}]
        for row, name, version, size in (("a", "Tool 10", "v1.10", "5 MB"),
                                         ("b", "Tool 2", "v1.2", "900 KB"),
                                         ("c", "Pinned", "v2", "1 GB")):
            self.table.insert("", "end", iid=row, values=(name, version, size))
            peer.insert("", "end", iid=row)
        self.save = Mock(return_value=True)
        self.order = TableOrder(self.table, self.actions, lambda: self.profiles,
                                self.save, lambda: True, Mock())

    def assert_order(self, expected):
        self.assertEqual(self.table.get_children(), expected)
        self.assertEqual(self.actions.tree.get_children(), expected)

    def test_version_sort_and_toggle_preserve_pinned_and_selected_row(self):
        self.table.selection_set("b")
        self.order.sort("local")
        self.assert_order(("c", "b", "a"))
        self.assertEqual(self.table.selection(), ("b",))
        self.assertEqual(self.table.heading("local", "text"), "local ▲")
        self.order.sort("local")
        self.assert_order(("c", "a", "b"))
        self.assertEqual(self.table.heading("local", "text"), "local ▼")

    def test_size_sort_uses_unit_values_and_refresh_keeps_order(self):
        self.order.sort("size")
        self.assert_order(("c", "b", "a"))
        self.table.set("a", "size", "100 B")
        self.order.apply()
        self.assert_order(("c", "a", "b"))
        self.assertEqual(self.table.set("c", "name"), "↑ Pinned")

    def test_pin_is_saved_and_failed_save_rolls_back(self):
        self.order.toggle_pin("a")
        self.assertTrue(self.profiles[0]["pinned"])
        self.save.assert_called_once()
        self.assert_order(("a", "c", "b"))
        self.order.toggle_pin("a")
        self.assert_order(("c", "a", "b"))
        self.assertEqual(self.table.set("a", "name"), "Tool 10")
        self.save.return_value = False
        self.order.toggle_pin("b")
        self.assertFalse(self.profiles[1]["pinned"])
        self.assert_order(("c", "a", "b"))

    def test_busy_blocks_sort_and_pin(self):
        self.order.allowed = lambda: False
        self.order.sort("local")
        self.order.toggle_pin("a")
        self.assertIsNone(self.order.column)
        self.save.assert_not_called()

    def test_installer_load_preserves_pin_and_natural_numeric_sort(self):
        rows = load_profiles([{"repository": "https://github.com/owner/tool", "pinned": True}])
        self.assertTrue(rows[0]["pinned"])
        self.assertLess(sort_value("Tool 2", "name"), sort_value("Tool 10", "name"))
        self.assertLess(sort_value("900 KB", "size"), sort_value("5 MB", "size"))
