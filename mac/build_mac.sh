#!/usr/bin/env bash
# ==============================================================================
# CPA Mac Manager - 一键构建与打包脚本
# 支持目标：
#   ./build_mac.sh        -> 构建真正原生 macOS .app 应用程序包并生成 .dmg 镜像 (默认)
#   ./build_mac.sh app    -> 仅构建原生 .app 应用程序包
#   ./build_mac.sh dmg    -> 构建原生 .app 并制作 .dmg 安装盘映像
#   ./build_mac.sh cli    -> 构建单文件独立命令行可执行文件
#   ./build_mac.sh pyapp  -> 使用 Python/PyInstaller 构建应用包
#   ./build_mac.sh all    -> 同时构建原生桌面 .app, .dmg 和 cli 独立二进制
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:-dmg}"
ARCH="$(uname -m)"

cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  CPA Mac 管理器 - 原生应用构建脚本"
echo "  架构: $ARCH | 模式: $TARGET"
echo "=========================================================="

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "⚠️  警告: 本脚本专用于 macOS 系统。"
    echo "   当前系统为: $(uname -s)"
fi

DIST_DIR="$SCRIPT_DIR/dist"
mkdir -p "$DIST_DIR"
TAG="${RELEASE_TAG:-${TAG:-${GITHUB_REF_NAME:-v1.5.16}}}"
if [[ "$TAG" == "main" || "$TAG" == "master" || -z "$TAG" ]]; then
    TAG="v1.5.16"
fi

# 1. 图标生成 (从 assets/app-icon.png 转为 macOS .icns)
ICNS_FILE="$SCRIPT_DIR/app-icon.icns"
PNG_SOURCE="$REPO_ROOT/assets/app-icon.png"

ensure_icons() {
    if [[ ! -f "$ICNS_FILE" ]] && [[ -f "$PNG_SOURCE" ]]; then
        if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
            echo "==> 正在将 PNG 图标转换为 macOS 原生 .icns 图标..."
            local ICONSET_DIR
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
    if [[ -f "$ICNS_FILE" ]]; then
        mkdir -p "$SCRIPT_DIR/tauri/src-tauri/icons"
        cp -f "$ICNS_FILE" "$SCRIPT_DIR/tauri/src-tauri/icons/icon.icns"
    fi
}

ensure_python_venv() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ 错误: 未检测到 python3。"
        exit 1
    fi
    local VENV_DIR="$SCRIPT_DIR/.venv"
    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
    fi
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
    pip install -q --upgrade pip
    pip install -q -r "$SCRIPT_DIR/requirements.txt" pyinstaller
}

build_native_tauri() {
    ensure_icons
    echo "==> 正在使用原生工具链构建真正的 macOS 原生桌面应用程序 (绝不打开外部浏览器)..."
    chmod +x "$SCRIPT_DIR/tauri/build.sh"
    "$SCRIPT_DIR/tauri/build.sh"

    local TAURI_APP="$SCRIPT_DIR/tauri/src-tauri/target/release/bundle/macos/CPA Mac Manager.app"
    local TAURI_DMG
    TAURI_DMG="$(ls -1 "$SCRIPT_DIR"/tauri/src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | head -n 1 || true)"

    if [[ -d "$TAURI_APP" ]]; then
        rm -rf "$DIST_DIR/CPA Mac Manager.app"
        cp -Rf "$TAURI_APP" "$DIST_DIR/"
        echo "✅ 原生应用程序生成成功: $DIST_DIR/CPA Mac Manager.app"
    fi

    if [[ -n "$TAURI_DMG" && -f "$TAURI_DMG" ]]; then
        local STANDARD_DMG="$DIST_DIR/CPA-Mac-Manager-${TAG}-darwin-${ARCH}.dmg"
        cp -f "$TAURI_DMG" "$STANDARD_DMG"
        echo "✅ 原生 DMG 安装镜像生成成功: $STANDARD_DMG"
    fi
}

build_cli() {
    ensure_icons
    ensure_python_venv
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

build_pyapp() {
    ensure_icons
    ensure_python_venv
    echo "==> 正在使用 Python/PyInstaller 构建应用包..."
    pyinstaller --clean --noconfirm "$SCRIPT_DIR/CPA_Mac_Manager.spec"
    echo "✅ Python 应用包构建成功: $DIST_DIR/CPA Mac Manager.app"
}

case "$TARGET" in
    app|dmg|tauri)
        build_native_tauri
        ;;
    cli)
        build_cli
        ;;
    pyapp)
        build_pyapp
        ;;
    all)
        build_native_tauri
        build_cli
        ;;
    *)
        echo "❌ 未知构建目标: $TARGET (可选值: app, dmg, cli, pyapp, all)"
        exit 1
        ;;
esac

echo "=========================================================="
echo "🎉 原生构建完成！"
echo "产物目录: $DIST_DIR"
if [[ -d "$DIST_DIR/CPA Mac Manager.app" ]]; then
    echo " • 原生桌面应用程序: $DIST_DIR/CPA Mac Manager.app"
    echo "   双击即以独立原生窗口运行（绝不打开外部浏览器）。"
fi
if ls "$DIST_DIR"/*.dmg >/dev/null 2>&1; then
    echo " • 原生 DMG 磁盘镜像: $(ls -1 "$DIST_DIR"/*.dmg | head -n 1)"
    echo "   双击挂载后，可直接拖拽至 Applications 完成安装分发。"
fi
if [[ -f "$DIST_DIR/cpa-mac-manager" ]]; then
    echo " • 单文件 CLI 二进制: $DIST_DIR/cpa-mac-manager"
fi
echo ""
echo "💡 提示: 如果在 macOS 上打开时提示“已损坏”或“无法验证开发者”，"
echo "   可打开终端执行以下命令解除系统的 Gatekeeper 隔离限制:"
echo "   sudo xattr -rd com.apple.quarantine \"/Applications/CPA Mac Manager.app\""
echo "=========================================================="
