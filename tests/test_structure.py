"""Regression checks for source entry point, path stability and module boundaries."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cpa_manager.core.paths import ROOT


class StructureTests(unittest.TestCase):
    def test_source_entry_preserves_root_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "source-smoke.json"
            result = subprocess.run([sys.executable, str(ROOT / "manager.py"),
                                     "--smoke-report", str(report)], cwd=directory,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(Path(data["root"]), ROOT)
            self.assertFalse(data["frozen"])
            self.assertEqual(data["tabs"], ["CLIProxyAPI", "CPA-Manager-Plus", "免安装软件", "安装向导软件"])

    def test_core_and_backends_do_not_import_higher_layers(self):
        rules = {"core": ("cpa_manager.backends", "cpa_manager.ui", "cpa_manager.app", "cpa_manager.config"),
                 "backends": ("cpa_manager.ui", "cpa_manager.app", "cpa_manager.config", "tkinter")}
        for layer, forbidden in rules.items():
            for path in (ROOT / "cpa_manager" / layer).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                               else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                    for name in modules:
                        with self.subTest(path=path.name, module=name):
                            self.assertFalse(any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden))
