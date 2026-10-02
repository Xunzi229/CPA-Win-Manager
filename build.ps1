param(
    [string]$OutputDirectory = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

function Invoke-ManagerBuild {
    param(
        [string]$TargetExecutable,
        [scriptblock]$Build,
        [int]$ShutdownTimeoutSeconds = 60
    )
    $targetPath = [IO.Path]::GetFullPath($TargetExecutable)
    $processName = [IO.Path]::GetFileNameWithoutExtension($targetPath)
    $runningManagers = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq $targetPath
    })
    if ($runningManagers.Count -gt 0) {
        Write-Host 'Closing the running manager before rebuilding...'
        foreach ($managerProcess in $runningManagers) {
            if (-not $managerProcess.HasExited) {
                # PyInstaller also has a parent process without a window.
                # Closing the GUI child lets both processes exit normally.
                $null = $managerProcess.CloseMainWindow()
            }
        }
        $deadline = [DateTime]::UtcNow.AddSeconds($ShutdownTimeoutSeconds)
        foreach ($managerProcess in $runningManagers) {
            $remaining = [Math]::Max(0, [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalMilliseconds))
            if (-not $managerProcess.WaitForExit($remaining)) {
                throw 'The manager is still finishing an operation. Build stopped without forcing it to exit.'
            }
        }
    }
    & $Build
    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        throw 'Build did not produce the manager executable.'
    }
    if ($runningManagers.Count -gt 0) {
        Write-Host 'Build complete. Reopening the manager...'
        Start-Process -FilePath $targetPath -WorkingDirectory ([IO.Path]::GetDirectoryName($targetPath)) -WindowStyle Normal
    }
}

$distRoot = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $PSScriptRoot $OutputDirectory }
$distRoot = [IO.Path]::GetFullPath($distRoot)
$buildRoot = Join-Path $PSScriptRoot '.build'
$icon = Join-Path $PSScriptRoot 'assets\app-icon.ico'
$python = Join-Path $buildRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $buildRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create build environment.' }
}
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt') pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw 'Failed to install build dependencies.' }
$versionFile = Join-Path $buildRoot 'app-version.json'
Push-Location $PSScriptRoot
try {
    & $python -c "import json, os, sys; from cpa_manager.core.version import SOURCE_VERSION; from cpa_manager.backends.cli import version_key; version = os.environ.get('GITHUB_REF_NAME', '') if os.environ.get('GITHUB_REF_TYPE') == 'tag' else SOURCE_VERSION; assert version_key(version) is not None, 'Invalid release version'; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps({'version': version.removeprefix('v')}))" $versionFile
    if ($LASTEXITCODE -ne 0) { throw 'Failed to generate application version.' }
} finally { Pop-Location }
Invoke-ManagerBuild -TargetExecutable (Join-Path $distRoot 'CPA-Unified-Manager.exe') -Build {
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name CPA-Unified-Manager --icon $icon --add-data "$icon;assets" --add-data "$versionFile;." --distpath $distRoot --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot (Join-Path $PSScriptRoot 'manager.py')
    if ($LASTEXITCODE -ne 0) { throw 'Build failed. See the build output for details.' }
}
