#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${RELEASE_TAG:?请设置 RELEASE_TAG，例如 v1.5.14}"
PACKAGE_ARCH="${PACKAGE_ARCH:-$(uname -m)}"
export RELEASE_TAG PACKAGE_ARCH

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "$PACKAGE_ARCH" ]]; then
    echo "请使用与 PACKAGE_ARCH=$PACKAGE_ARCH 相同架构的 macOS runner。" >&2
    exit 1
fi
case "$PACKAGE_ARCH" in
    arm64|x86_64) ;;
    *) echo "不支持的 Mac 架构：$PACKAGE_ARCH" >&2; exit 1 ;;
esac
python3 -c 'import os, platform; assert platform.machine() == os.environ["PACKAGE_ARCH"], "Python 架构与 runner 不一致"'

export APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
bash "$SCRIPT_DIR/build.sh"

BUNDLE_DIR="$SCRIPT_DIR/src-tauri/target/release/bundle"
APP="$BUNDLE_DIR/macos/CPA Mac Manager.app"
DMGS=("$BUNDLE_DIR/dmg/CPA Mac Manager_${RELEASE_TAG#v}_"*.dmg)
if [[ ${#DMGS[@]} -ne 1 || ! -f "${DMGS[0]}" ]]; then
    echo "找不到当前版本唯一的 DMG 安装包。" >&2
    exit 1
fi
for binary in "$APP/Contents/MacOS/cpa-mac-manager" "$APP/Contents/Resources/cpa-native-worker"; do
    test "$(lipo -archs "$binary")" = "$PACKAGE_ARCH"
done
codesign --verify --deep --strict "$APP"
hdiutil verify "${DMGS[0]}"

DIST="$SCRIPT_DIR/../dist"
mkdir -p "$DIST"
ARCHIVE="$DIST/CPA-Mac-Manager-${RELEASE_TAG}-darwin-${PACKAGE_ARCH}"
cp "${DMGS[0]}" "$ARCHIVE.dmg"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE.zip"

"$SCRIPT_DIR/../.venv/bin/python" - "$APP" "$ARCHIVE.zip" <<'PY'
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import zipfile

app = Path(sys.argv[1])
version = os.environ["RELEASE_TAG"].removeprefix("v")
with (app / "Contents/Info.plist").open("rb") as handle:
    assert plistlib.load(handle)["CFBundleShortVersionString"] == version
request = {"scope": "system", "action": "version", "source_root": str(app), "config": {
    "proxy_enabled": False, "proxy": "", "prerelease": False,
    "download_workers": 4, "download_directory": "", "cli_directory": "",
    "plus_directory": "", "portable": [], "packages": [],
}}
env = {key: value for key, value in os.environ.items() if key not in ("RELEASE_TAG", "GITHUB_REF_NAME")}
result = subprocess.run([str(app / "Contents/Resources/cpa-native-worker")],
                        input=json.dumps(request) + "\n", capture_output=True, text=True,
                        check=True, timeout=30, env=env)
event = json.loads(result.stdout.strip().splitlines()[-1])
assert event == {"type": "done", "result": {"version": version}}, event
with zipfile.ZipFile(sys.argv[2]) as archive:
    assert archive.testzip() is None
    for name in ("Contents/Info.plist", "Contents/MacOS/cpa-mac-manager", "Contents/Resources/cpa-native-worker"):
        assert f"{app.name}/{name}" in archive.namelist(), name
print(f"发布包校验通过：{version} / {os.environ['PACKAGE_ARCH']}")
PY
