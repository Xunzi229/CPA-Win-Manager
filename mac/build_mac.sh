#!/usr/bin/env bash
# ==============================================================================
# CPA Mac Manager - 一键构建与打包脚本
# 支持目标：
#   ./build_mac.sh        -> 构建 macOS .app 应用程序包并生成 .dmg 镜像
#   ./build_mac.sh app    -> 仅构建 .app 应用程序包
#   ./build_mac.sh dmg    -> 构建 .app 并制作 .dmg 安装盘映像
#   ./build_mac.sh cli    -> 构建单文件独立命令行可执行文件
#   ./build_mac.sh all    -> 同时构建 .app, .dmg 和 cli 独立二进制
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:-dmg}"
ARCH="$(uname -m)"

cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  CPA Mac 管理器 - 打包构建脚本"
echo "  架构: $ARCH | 模式: $TARGET"
echo "=========================================================="

# 1. 检查操作系统
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "⚠️  警告: 本脚本专用于 macOS 系统。"
    echo "   当前系统为: $(uname -s)"
fi

# 2. 检查 Python 环境
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 错误: 未检测到 python3，请先安装 Python 3.10+ (可通过 brew install python 或官网安装)。"
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PYTHON_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"

if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 10 ]]; }; then
    echo "❌ 错误: Python 版本需 >= 3.10，当前版本为: $PYTHON_VERSION"
    exit 1
fi
echo "==> 检测到 Python $PYTHON_VERSION"

# 3. 准备虚拟环境
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> 正在创建 Python 虚拟环境 ($VENV_DIR)..."
    python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

echo "==> 正在安装/检查依赖项 (requirements.txt + pyinstaller)..."
pip install -q --upgrade pip
pip install -q -r "$SCRIPT_DIR/requirements.txt" pyinstaller

# 4. 图标生成 (从 assets/app-icon.png 转为 macOS .icns)
ICNS_FILE="$SCRIPT_DIR/app-icon.icns"
PNG_SOURCE="$REPO_ROOT/assets/app-icon.png"

if [[ ! -f "$ICNS_FILE" ]] && [[ -f "$PNG_SOURCE" ]]; then
    if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        echo "==> 正在将 PNG 图标转换为 macOS 原生 .icns 图标..."
        ICONSET_DIR="$(mktemp -d -t cpa_iconset.XXXXXX).iconset"
        mkdir -p "$ICONSET_DIR"
        sips -z 16 16     "$PNG_SOURCE" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null 2>&1
        sips -z 32 32     "$PNG_SOURCE" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null 2>&1
        sips -z 32 32     "$PNG_SOURCE" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null 2>&1
        sips -z 64 64     "$PNG_SOURCE" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null 2>&1
        sips -z 128 128   "$PNG_SOURCE" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null 2>&1
        sips -z 256 256   "$PNG_SOURCE" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null 2>&1
        sips -z 256 256   "$PNG_SOURCE" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null 2>&1
        sips -z 512 512   "$PNG_SOURCE" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null 2>&1
        sips -z 512 512   "$PNG_SOURCE" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null 2>&1
        sips -z 1024 1024 "$PNG_SOURCE" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null 2>&1
        iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE"
        rm -rf "$ICONSET_DIR"
        echo "==> 图标生成完成: $ICNS_FILE"
    fi
fi

# 5. 执行构建
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_DIR="$SCRIPT_DIR/build"
TAG="${RELEASE_TAG:-${TAG:-${GITHUB_REF_NAME:-v1.5.14}}}"
if [[ "$TAG" == "main" || "$TAG" == "master" || -z "$TAG" ]]; then
    TAG="v1.5.14"
fi

build_app() {
    echo "==> [1/2] 正在构建 macOS 原生应用包 (CPA Mac Manager.app)..."
    pyinstaller --clean --noconfirm "$SCRIPT_DIR/CPA_Mac_Manager.spec"
    echo "✅ 应用包构建成功: $DIST_DIR/CPA Mac Manager.app"
}

build_dmg() {
    build_app
    local APP_PATH="$DIST_DIR/CPA Mac Manager.app"
    local DMG_NAME="CPA-Mac-Manager-${TAG}-darwin-${ARCH}.dmg"
    local DMG_PATH="$DIST_DIR/$DMG_NAME"

    if ! command -v hdiutil >/dev/null 2>&1; then
        echo "⚠️  未找到 hdiutil 命令，跳过 DMG 制作。"
        return 0
    fi

    echo "==> [2/2] 正在制作 DMG 镜像 ($DMG_NAME)..."
    local STAGING_DIR
    STAGING_DIR="$(mktemp -d -t cpa_dmg_staging.XXXXXX)"
    cp -R "$APP_PATH" "$STAGING_DIR/"
    ln -s /Applications "$STAGING_DIR/Applications"

    rm -f "$DMG_PATH"
    hdiutil create -volname "CPA Mac Manager" \
                   -srcfolder "$STAGING_DIR" \
                   -ov \
                   -format UDZO \
                   "$DMG_PATH" >/dev/null

    rm -rf "$STAGING_DIR"
    echo "✅ DMG 安装镜像制作完成: $DMG_PATH"
}

build_zip() {
    local APP_PATH="$DIST_DIR/CPA Mac Manager.app"
    local ZIP_NAME="CPA-Mac-Manager-${TAG}-darwin-${ARCH}.zip"
    local ZIP_PATH="$DIST_DIR/$ZIP_NAME"
    if [[ ! -d "$APP_PATH" ]]; then
        build_app
    fi
    echo "==> 正在制作 ZIP 归档 ($ZIP_NAME)..."
    (cd "$DIST_DIR" && zip -r -q "$ZIP_NAME" "CPA Mac Manager.app")
    echo "✅ ZIP 归档制作完成: $ZIP_PATH"
}

build_cli() {
    echo "==> 正在构建独立命令行二进制 (cpa-mac-manager)..."
    local ICON_ARG=()
    if [[ -f "$ICNS_FILE" ]]; then
        ICON_ARG=(--icon "$ICNS_FILE")
    fi
    pyinstaller --clean --noconfirm \
                --onefile \
                --console \
                --name "cpa-mac-manager" \
                "${ICON_ARG[@]}" \
                manager.py
    echo "✅ 命令行程序构建完成: $DIST_DIR/cpa-mac-manager"
}

case "$TARGET" in
    app)
        build_app
        ;;
    dmg)
        build_dmg
        ;;
    cli)
        build_cli
        ;;
    all)
        build_dmg
        build_zip
        build_cli
        ;;
    *)
        echo "❌ 未知构建目标: $TARGET (可选值: app, dmg, cli, all)"
        exit 1
        ;;
esac

echo "=========================================================="
echo "🎉 构建全部完成！"
echo "产物目录: $DIST_DIR"
if [[ -d "$DIST_DIR/CPA Mac Manager.app" ]]; then
    echo " • 原生应用程序: $DIST_DIR/CPA Mac Manager.app"
    echo "   双击即可直接启动，或拖入 /Applications 应用程序目录。"
fi
if ls "$DIST_DIR"/*.dmg >/dev/null 2>&1; then
    echo " • DMG 磁盘映像: $(ls -1 "$DIST_DIR"/*.dmg | head -n 1)"
    echo "   双击挂载后，可直接拖拽至 Applications 完成安装分发。"
fi
if [[ -f "$DIST_DIR/cpa-mac-manager" ]]; then
    echo " • 单文件二进制: $DIST_DIR/cpa-mac-manager"
    echo "   在终端中运行: ./dist/cpa-mac-manager"
fi
echo ""
echo "💡 提示: 如果在 macOS 上打开时提示“已损坏”或“无法验证开发者”，"
echo "   可打开终端执行以下命令解除系统的 Gatekeeper 隔离限制:"
echo "   sudo xattr -rd com.apple.quarantine \"/Applications/CPA Mac Manager.app\""
echo "=========================================================="
