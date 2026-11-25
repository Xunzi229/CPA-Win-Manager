"""Administrator restart and single-instance handoff."""
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import psutil

from cpa_manager.core import elevation


class ElevationTests(unittest.TestCase):
    def test_detects_elevated_process(self):
        check = Mock(return_value=1)
        with patch.object(elevation.ctypes, "WinDLL", return_value=SimpleNamespace(IsUserAnAdmin=check)):
            self.assertTrue(elevation.is_elevated())
        check.assert_called_once_with()

    def test_detects_standard_process(self):
        check = Mock(return_value=0)
        with patch.object(elevation.ctypes, "WinDLL", return_value=SimpleNamespace(IsUserAnAdmin=check)):
            self.assertFalse(elevation.is_elevated())

    def test_launch_uses_runas_and_waits_for_current_pid(self):
        execute = Mock(return_value=42)
        with patch.object(elevation.ctypes, "WinDLL", return_value=SimpleNamespace(ShellExecuteW=execute)), \
             patch.object(elevation.sys, "frozen", True, create=True), \
             patch.object(elevation.sys, "executable", "C:\\Tools\\CPA-Unified-Manager.exe"), \
             patch.object(elevation.os, "getpid", return_value=1234):
            elevation.launch_elevated_manager()
        args = execute.call_args.args
        self.assertEqual(args[1], "runas")
        self.assertEqual(Path(args[2]), Path("C:\\Tools\\CPA-Unified-Manager.exe"))
        self.assertEqual(args[3], "--wait-for-pid 1234")

    def test_waits_for_old_process_to_exit(self):
        previous = Mock()
        with patch.object(elevation.psutil, "Process", return_value=previous):
            elevation.wait_for_previous_manager(1234)
        previous.wait.assert_called_once_with(timeout=120)

    def test_wait_timeout_prevents_duplicate_manager(self):
        previous = Mock()
        previous.wait.side_effect = psutil.TimeoutExpired(120)
        with patch.object(elevation.psutil, "Process", return_value=previous):
            with self.assertRaisesRegex(RuntimeError, "旧管理器尚未退出"):
                elevation.wait_for_previous_manager(1234)
