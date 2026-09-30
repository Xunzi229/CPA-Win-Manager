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
$versionFile = Join-Path $buildRoot 'app-version.json'
Push-Location $PSScriptRoot
try {
    & $python -c "import json, os, sys; from app_version import SOURCE_VERSION; from cli_backend import version_key; version = os.environ.get('GITHUB_REF_NAME', '') if os.environ.get('GITHUB_REF_TYPE') == 'tag' else SOURCE_VERSION; assert version_key(version) is not None, 'Invalid release version'; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps({'version': version.removeprefix('v')}))" $versionFile
    if ($LASTEXITCODE -ne 0) { throw 'Failed to generate application version.' }
} finally { Pop-Location }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name CPA-Unified-Manager --icon $icon --add-data "$icon;assets" --add-data "$versionFile;." --distpath $PSScriptRoot --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot (Join-Path $PSScriptRoot 'manager.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed. Close the running manager before rebuilding.' }
