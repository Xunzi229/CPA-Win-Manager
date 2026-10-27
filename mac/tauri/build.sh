#!/usr/bin/env bash
# ==============================================================================
# CPA Mac Manager - Tauri 原生版一键打包脚本
# 支持在 macOS 环境下直接运行：
#   ./build.sh          -> 安装依赖并构建原生 macOS .app 与 .dmg
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  CPA Mac 管理器 (Tauri 原生桌面版) - 打包构建脚本"
echo "  架构: $(uname -m) | 平台: $(uname -s)"
echo "=========================================================="

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "⚠️  注意: 构建原生 macOS .app / .dmg 建议在 macOS 系统或 CI 下运行。"
fi

# 1. 检查 Node.js 与 npm
if command -v npm >/dev/null 2>&1; then
    echo "==> [1/3] 正在安装 Node 依赖..."
    npm ci --silent
else
    echo "❌ 未检测到 npm，请先安装 Node.js 18+。"
    exit 1
fi

# 2. 检查 Rust 环境
if ! command -v cargo >/dev/null 2>&1; then
    echo "❌ 未检测到 Rust/Cargo，请先安装 Rust。"
    exit 1
fi
echo "==> 检测到 Cargo: $(cargo --version)"

node --check "$SCRIPT_DIR/src/app.js"
node --test "$SCRIPT_DIR/../tests/test_mac_frontend.cjs"
PYTHON="$SCRIPT_DIR/../.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    python3 -m venv "$SCRIPT_DIR/../.venv"
fi
"$PYTHON" - "$SCRIPT_DIR" <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root.parent))
from cpa_mac.config import version_key

version = os.environ.get("RELEASE_TAG") or json.loads((root / "src-tauri/tauri.conf.json").read_text())["version"]
if version_key(version) is None:
    raise SystemExit(f"无效的发布版本：{version}")
version = version.removeprefix("v")
build = root.parent / "build"
build.mkdir(exist_ok=True)
for name in ("app-version.json", "tauri-release.json"):
    (build / name).write_text(json.dumps({"version": version}), encoding="utf-8")
print(f"构建版本：{version}")
PY
"$PYTHON" -m pip install -q psutil==7.2.2 PyYAML==6.0.3 pyinstaller certifi
"$PYTHON" -m unittest discover -s "$SCRIPT_DIR/../tests" -p 'test_mac_*.py'
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --console \
    --name cpa-native-worker --paths "$SCRIPT_DIR/.." \
    --add-data "$SCRIPT_DIR/../build/app-version.json:." \
    --distpath "$SCRIPT_DIR/src-tauri/resources" \
    --workpath "$SCRIPT_DIR/../build/native-worker" \
    --specpath "$SCRIPT_DIR/../build" \
    "$SCRIPT_DIR/../cpa_mac/native_worker.py"
cargo test --manifest-path "$SCRIPT_DIR/src-tauri/Cargo.toml" --lib

# 3. 运行 Tauri 构建
echo "==> [2/3] 正在执行 Tauri 原生应用构建与 DMG 打包..."
"$SCRIPT_DIR/node_modules/.bin/tauri" build --config "$SCRIPT_DIR/../build/tauri-release.json"

echo "=========================================================="
echo "🎉 Tauri 原生打包完成！"
echo "产物路径位于: src-tauri/target/release/bundle/"
if [[ -d "src-tauri/target/release/bundle/dmg" ]]; then
    echo " • DMG 磁盘安装镜像: $(ls -1 src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | head -n 1)"
fi
if [[ -d "src-tauri/target/release/bundle/macos" ]]; then
    echo " • macOS 原生应用程序: src-tauri/target/release/bundle/macos/CPA Mac Manager.app"
    bash "$SCRIPT_DIR/../install_local_app.sh" "$SCRIPT_DIR/src-tauri/target/release/bundle/macos/CPA Mac Manager.app"
fi
