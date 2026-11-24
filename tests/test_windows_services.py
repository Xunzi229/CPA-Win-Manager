"""Windows services using portable files are stopped and restored."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from cpa_manager.core import windows_services


class WindowsServicesTests(unittest.TestCase):
    def test_service_access_denied_explains_admin_requirement(self):
        result = SimpleNamespace(returncode=5)
        service = SimpleNamespace(status=Mock(return_value="running"))
        with patch.object(windows_services.subprocess, "run", return_value=result), \
             patch.object(windows_services.psutil, "win_service_get", return_value=service):
            with self.assertRaisesRegex(RuntimeError, "请以管理员身份运行管理器"):
                windows_services._change_service("FlClashHelperService", "stop")

    def test_only_running_service_with_updated_executable_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SimpleNamespace(as_dict=Mock(return_value={
                "name": "FlClashHelperService", "status": "running",
                "binpath": f'"{root / "FlClashHelperService.exe"}" --owner-sid abc'}))
            unrelated = SimpleNamespace(as_dict=Mock(return_value={
                "name": "Unrelated", "status": "running", "binpath": str(root / "other.exe")}))
            with patch.object(windows_services.psutil, "win_service_iter", return_value=[service, unrelated]):
                self.assertEqual(windows_services._matching_services(
                    root, ["FlClashHelperService.exe"]), ["FlClashHelperService"])
            with patch.object(windows_services.psutil, "win_service_iter", return_value=[service, unrelated]):
                self.assertEqual(windows_services._matching_services(root, None),
                                 ["FlClashHelperService", "Unrelated"])

    def test_service_is_restored_after_install_failure(self):
        report = Mock()
        with patch.object(windows_services, "_matching_services", return_value=["FlClashHelperService"]), \
             patch.object(windows_services, "_change_service") as change:
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                with windows_services.paused_target_services(Path("C:/apps"), [], report):
                    raise RuntimeError("install failed")
        self.assertEqual(change.call_args_list, [call("FlClashHelperService", "stop"),
                                                 call("FlClashHelperService", "start")])
