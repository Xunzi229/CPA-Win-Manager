$ErrorActionPreference = 'Stop'
$buildRoot = Join-Path $PSScriptRoot '.build'
$icon = Join-Path $PSScriptRoot 'assets\app-icon.ico'
$python = Join-Path $buildRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $buildRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create build environment.' }
}
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt') pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw 'Failed to install build dependencies.' }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name CPA-Unified-Manager --icon $icon --add-data "$icon;assets" --distpath $PSScriptRoot --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot (Join-Path $PSScriptRoot 'manager.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed. Close the running manager before rebuilding.' }
