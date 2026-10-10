# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent
ASSETS_DIR = REPO_ROOT / "assets"

# 查找应用图标
icon_file = None
for candidate in [
    SPEC_DIR / "app-icon.icns",
    ASSETS_DIR / "app-icon.icns",
    SPEC_DIR / "app-icon.png",
    ASSETS_DIR / "app-icon.png",
]:
    if candidate.exists():
        icon_file = str(candidate)
        break

a = Analysis(
    ['manager.py'],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'cpa_mac',
        'cpa_mac.app',
        'cpa_mac.config',
        'cpa_mac.state',
        'cpa_mac.webapp',
        'cpa_mac.backends.github',
        'cpa_mac.backends.service',
        'cpa_mac.backends.software',
        'cpa_mac.core.archive',
        'cpa_mac.core.lock',
        'cpa_mac.core.network',
        'cpa_mac.core.settings',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CPA Mac Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='CPA Mac Manager',
)

app = BUNDLE(
    coll,
    name='CPA Mac Manager.app',
    icon=icon_file,
    bundle_identifier='com.routerforme.cpamacmanager',
    info_plist={
        'CFBundleName': 'CPA Mac Manager',
        'CFBundleDisplayName': 'CPA Mac 管理器',
        'CFBundleIdentifier': 'com.routerforme.cpamacmanager',
        'CFBundleVersion': os.environ.get("GITHUB_REF_NAME", "1.5.14").removeprefix("v"),
        'CFBundleShortVersionString': os.environ.get("GITHUB_REF_NAME", "1.5.14").removeprefix("v"),
        'CFBundleExecutable': 'CPA Mac Manager',
        'CFBundlePackageType': 'APPL',
        'NSHumanReadableCopyright': 'Copyright © 2026 Router For Me. All rights reserved.',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.developer-tools',
    },
)
