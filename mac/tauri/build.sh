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
    npm install --silent
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

# 3. 运行 Tauri 构建
echo "==> [2/3] 正在执行 Tauri 原生应用构建与 DMG 打包..."
npx @tauri-apps/cli build

echo "=========================================================="
echo "🎉 Tauri 原生打包完成！"
echo "产物路径位于: src-tauri/target/release/bundle/"
if [[ -d "src-tauri/target/release/bundle/dmg" ]]; then
    echo " • DMG 磁盘安装镜像: $(ls -1 src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | head -n 1)"
fi
if [[ -d "src-tauri/target/release/bundle/macos" ]]; then
    echo " • macOS 原生应用程序: src-tauri/target/release/bundle/macos/CPA Mac Manager.app"
fi