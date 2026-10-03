"""Installed software identification reads registry fixtures, never real software files."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cpa_manager.core.installed_software import installation_directory, match_installed, scan_installed, uninstall_command, launch_uninstaller, installed_update_available
from cpa_manager.backends.installer import load_profiles


class RegistryKey:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class InstalledSoftwareTests(unittest.TestCase):
    def test_update_comparison_handles_equal_unknown_four_part_and_prefixed_versions(self):
        for local, latest, expected in (("2.5.6", "v2.5.7", True), ("2.5.7", "v2.5.7", False),
                                        ("2.5.8", "v2.5.7", False), ("", "v2.5.7", False),
                                        ("未知", "v2.5.7", False), ("0.101.2362.0", "v0.101.2362.0", False),
                                        ("0.101.2362.0", "v0.102.0.0", True), ("0.38.0", "note-gen-v0.38.1", True),
                                        ("1.2.0-rc.1", "v1.2.0", True), ("1.2.0", "v1.2.0-rc.1", False)):
            with self.subTest(local=local, latest=latest):
                self.assertEqual(installed_update_available(local, latest), expected)

    def setUp(self):
        self.profile = {"name": "PowerToys", "repository": "https://github.com/microsoft/PowerToys"}
        self.record = {"id": "HKCU:256:PowerToys", "name": "PowerToys (Preview)",
                       "version": "0.101.0", "directory": "", "publisher": "Microsoft"}

    def test_unique_names_match_without_fuzzy_substrings(self):
        self.assertEqual(match_installed(self.profile, [self.record]), self.record)
        other = dict(self.record, id="helper", name="PowerToys Helper")
        self.assertIsNone(match_installed(self.profile, [other]))
        duplicate = dict(self.record, id="HKLM:256:PowerToys")
        self.assertIsNone(match_installed(self.profile, [self.record, duplicate]))

    def test_manual_association_overrides_names_and_does_not_switch_if_missing(self):
        profile = dict(self.profile, installed_id="manual")
        manual = dict(self.record, id="manual", name="Different display name", version="2.0")
        self.assertEqual(match_installed(profile, [self.record, manual]), manual)
        self.assertIsNone(match_installed(profile, [self.record]))
        newer = dict(manual, version="2.1")
        self.assertEqual(match_installed(profile, [newer])["version"], "2.1")

    def test_repository_name_matches_renamed_row_and_version_suffix(self):
        profile = {"name": "我的传输工具", "repository": "https://github.com/localsend/localsend"}
        record = dict(self.record, name="LocalSend version 1.18.2")
        self.assertEqual(match_installed(profile, [record]), record)

    def test_location_or_display_icon_without_running_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            icon = '"' + str(root / "app.exe") + '",0'
            self.assertEqual(installation_directory("", icon), str(root))
            self.assertEqual(installation_directory(str(root), "bad"), str(root))
            self.assertEqual(installation_directory("", "msiexec.exe /x {id}"), "")

    def test_uninstall_command_preserves_quoted_executable_and_converts_msi_maintenance(self):
        self.assertEqual(uninstall_command('"C:\\Program Files\\Tool\\uninstall.exe" /user'),
                         (r"C:\Program Files\Tool\uninstall.exe", "/user"))
        self.assertEqual(uninstall_command(r"MsiExec.exe /I{123} /passive"), ("MsiExec.exe", "/X{123} /passive"))
        self.assertEqual(uninstall_command(r"msiexec.exe /package {123}"), ("msiexec.exe", "/X {123}"))
        with self.assertRaises(ValueError):
            uninstall_command("")

    def test_uninstall_uses_current_registered_command_and_does_not_execute_when_missing(self):
        execute = Mock(return_value=33)
        record = dict(self.record, uninstall='"C:\\Tool\\uninstall.exe" /user')
        with patch("cpa_manager.core.installed_software.scan_installed", return_value=[record]), \
             patch("ctypes.WinDLL", return_value=SimpleNamespace(ShellExecuteW=execute)):
            launch_uninstaller(self.record)
        execute.assert_called_once_with(None, "open", r"C:\Tool\uninstall.exe", "/user", None, 1)
        with patch("cpa_manager.core.installed_software.scan_installed", return_value=[]), \
             patch("ctypes.WinDLL") as library:
            with self.assertRaises(ValueError):
                launch_uninstaller(self.record)
            library.assert_not_called()

    def test_loader_preserves_only_valid_association(self):
        source = dict(self.profile, installed_id=self.record["id"])
        self.assertEqual(load_profiles([source])[0]["installed_id"], self.record["id"])
        self.assertEqual(load_profiles([dict(source, installed_id={})])[0]["installed_id"], "")

    def test_both_registry_hives_and_views_are_read_and_duplicates_hidden_components_filtered(self):
        fixtures = {"App": {"DisplayName": "PowerToys", "DisplayVersion": "1.2", "InstallLocation": "", "DisplayIcon": ""},
                    "Hidden": {"DisplayName": "Helper", "SystemComponent": 1},
                    "MissingName": {"DisplayVersion": "1"}}
        opened = []
        def open_key(parent, name, reserved=0, access=0):
            if isinstance(parent, RegistryKey):
                return RegistryKey(parent.data[name])
            opened.append((parent, access))
            return RegistryKey(fixtures)
        def query_value(key, field):
            if field not in key.data:
                raise FileNotFoundError(field)
            return key.data[field], 1
        registry = SimpleNamespace(HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="machine",
                                   KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, KEY_READ=1,
                                   OpenKey=open_key, QueryInfoKey=lambda key: (len(key.data), 0, 0),
                                   EnumKey=lambda key, index: list(key.data)[index], QueryValueEx=query_value)
        with patch.dict(sys.modules, winreg=registry), patch("cpa_manager.core.installed_software.os.name", "nt"):
            rows = scan_installed()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["version"] for row in rows}, {"1.2"})
        self.assertEqual(opened, [("user", 257), ("user", 513), ("machine", 257), ("machine", 513)])
