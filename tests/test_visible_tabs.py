"""Configurable visibility of the four main tabs."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpa_manager.app import App
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.config import TAB_KEYS, parse_visible_tabs
from cpa_manager.core.settings_store import read_settings


class Notebook:
    def __init__(self, frames, selected):
        self.states = {frame: "normal" for frame in frames}
        self.selected = selected

    def tab(self, frame, state):
        self.states[frame] = state

    def select(self, frame=None):
        if frame is None:
            return self.selected
        self.selected = frame


class VisibleTabsTests(unittest.TestCase):
    def app(self, directory, visible):
        frames = {key: "." + key for key in TAB_KEYS}
        app = SimpleNamespace(profiles={}, proxy_settings={}, custom_profiles=[], installer_profiles=[],
                              installer_download_directory=directory, release_cache=ReleaseCache(),
                              settings_file=Path(directory) / "settings.json", visible_tabs=visible,
                              tab_frames=frames, notebook=Notebook(frames.values(), ".cli"))
        app.save = lambda: App.save(app)
        app.apply_visible_tabs = lambda: App.apply_visible_tabs(app)
        return app

    def test_parse_keeps_known_keys_in_fixed_order(self):
        self.assertEqual(parse_visible_tabs(["package", "x", "cli", "cli"]), ["cli", "package"])
        self.assertEqual(parse_visible_tabs(None), [])
        self.assertEqual(parse_visible_tabs("cli"), [])

    def test_hidden_selected_tab_switches_to_first_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory, list(TAB_KEYS))
            App.set_visible_tabs(app, ["portable", "package"])
            self.assertEqual(app.notebook.states, {".cli": "hidden", ".plus": "hidden",
                                                   ".portable": "normal", ".package": "normal"})
            self.assertEqual(app.notebook.selected, ".portable")
            self.assertEqual(read_settings(app.settings_file)["visible_tabs"], ["portable", "package"])

    def test_at_least_one_tab_is_required_and_failed_save_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory, ["cli", "plus"])
            with self.assertRaises(ValueError):
                App.set_visible_tabs(app, [])
            self.assertEqual(app.visible_tabs, ["cli", "plus"])
            with patch("cpa_manager.core.settings_store.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    App.set_visible_tabs(app, ["plus"])
            self.assertEqual(app.visible_tabs, ["cli", "plus"])
            self.assertEqual(app.notebook.states[".cli"], "normal")
