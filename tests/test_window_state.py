"""Window bounds persist without losing the normal geometry when maximized."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from cpa_manager.app import App
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.window_state import restore_window_state
from cpa_manager.core.settings_store import read_settings


class WindowStateTests(unittest.TestCase):
    def test_visible_normal_window_is_restored_exactly(self):
        value = {"width": 1120, "height": 840, "x": 210, "y": 60, "maximized": False}
        self.assertEqual(restore_window_state(value, (0, 0, 1920, 1080)), value)

    def test_window_on_negative_origin_monitor_is_restored(self):
        value = {"width": 1000, "height": 800, "x": -1700, "y": 100, "maximized": True}
        self.assertEqual(restore_window_state(value, (-1920, 0, 0, 1080)), value)

    def test_disconnected_or_smaller_monitor_keeps_window_visible(self):
        value = {"width": 2000, "height": 1200, "x": -1700, "y": 900, "maximized": False}
        restored = restore_window_state(value, (0, 0, 1280, 720))
        self.assertGreaterEqual(restored["x"], 0)
        self.assertGreaterEqual(restored["y"], 0)
        self.assertLessEqual(restored["x"] + restored["width"], 1280)
        self.assertLessEqual(restored["y"] + restored["height"], 720)

    def test_invalid_preferences_fall_back_to_default_size(self):
        for value in (None, {}, {"width": "bad"}, {"width": -10, "height": 800, "x": 0, "y": 0}):
            self.assertEqual(restore_window_state(value, (0, 0, 1920, 1080))["width"], 940)

    def test_maximize_and_minimize_preserve_normal_bounds_and_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            window = Mock()
            window.state.return_value = "normal"
            window.winfo_width.return_value = 1100
            window.winfo_height.return_value = 800
            window.winfo_x.return_value = 120
            window.winfo_y.return_value = 50
            app = SimpleNamespace(window=window, window_preferences={}, profiles={}, proxy_settings={},
                                  custom_profiles=[], installer_profiles=[], installer_download_directory=directory,
                                  release_cache=ReleaseCache(), settings_file=Path(directory) / "settings.json")
            App.capture_window_state(app)
            expected = dict(app.window_preferences, maximized=True)
            window.state.return_value = "zoomed"
            window.winfo_width.return_value = 1920
            App.capture_window_state(app)
            self.assertEqual(app.window_preferences, expected)
            window.state.return_value = "iconic"
            App.capture_window_state(app)
            self.assertEqual(app.window_preferences, expected)
            App.save(app)
            restored = read_settings(app.settings_file)["window"]
            self.assertEqual(restore_window_state(restored, (0, 0, 1920, 1080)), expected)

    def test_child_widget_changes_do_not_schedule_window_saves(self):
        app = SimpleNamespace(window=Mock(), capture_window_state=Mock(), save_window_state=Mock(), window_save_timer=None)
        App.window_changed(app, SimpleNamespace(widget=Mock()))
        app.capture_window_state.assert_not_called()
        App.window_changed(app, SimpleNamespace(widget=app.window))
        app.capture_window_state.assert_called_once()
