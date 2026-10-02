"""Build lifecycle closes only its target and restarts only after success."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cpa_manager.core.paths import ROOT


class BuildLifecycleTests(unittest.TestCase):
    def scenario(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "lifecycle.ps1"
            script.write_text(r'''
param([string]$Source, [string]$Target, [string]$Mode)
$ErrorActionPreference = 'Stop'
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'PowerShell syntax error' }
$definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-ManagerBuild' }, $true)
Invoke-Expression $definition.Extent.Text
$script:events = [Collections.Generic.List[string]]::new()
$script:allowShutdown = $Mode -ne 'timeout'
function Get-Process {
    [CmdletBinding()] param([string]$Name)
    if ($Mode -eq 'not-running') { return }
    $path = if ($Mode -eq 'other-directory') { Join-Path (Split-Path $Target) 'other/CPA-Unified-Manager.exe' } else { $Target }
    $process = [pscustomobject]@{ Path = $path; HasExited = $false }
    $process | Add-Member ScriptMethod CloseMainWindow {
        $script:events.Add('close')
        return $true
    }
    $process | Add-Member ScriptMethod WaitForExit {
        param($milliseconds)
        $script:events.Add('wait')
        return $script:allowShutdown
    }
    return $process
}
function Start-Process {
    param([string]$FilePath, [string]$WorkingDirectory, [string]$WindowStyle)
    if ($FilePath -ne $Target -or $WorkingDirectory -ne (Split-Path $Target) -or $WindowStyle -ne 'Normal') { throw 'Wrong restart target' }
    $script:events.Add('start')
}
$errorMessage = ''
try {
    Invoke-ManagerBuild -TargetExecutable $Target -ShutdownTimeoutSeconds 0 -Build {
        $script:events.Add('build')
        if ($Mode -eq 'failure') { throw 'Build failed' }
        Set-Content -LiteralPath $Target -Value 'new executable'
    }
} catch { $errorMessage = $_.Exception.Message }
@{ events = @($script:events.ToArray()); error = $errorMessage } | ConvertTo-Json -Compress
''', encoding="utf-8-sig")
            result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                                     str(ROOT / "build.ps1"), str(Path(directory) / "CPA-Unified-Manager.exe"), mode],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_running_target_closes_builds_then_restarts(self):
        result = self.scenario("running")
        self.assertEqual(result["events"], ["close", "wait", "build", "start"])
        self.assertEqual(result["error"], "")

    def test_other_directory_and_inactive_manager_are_not_restarted(self):
        for mode in ("other-directory", "not-running"):
            with self.subTest(mode=mode):
                self.assertEqual(self.scenario(mode)["events"], ["build"])

    def test_close_timeout_prevents_build_and_restart(self):
        result = self.scenario("timeout")
        self.assertEqual(result["events"], ["close", "wait"])
        self.assertIn("without forcing", result["error"])

    def test_build_failure_does_not_restart(self):
        result = self.scenario("failure")
        self.assertEqual(result["events"], ["close", "wait", "build"])
        self.assertEqual(result["error"], "Build failed")
