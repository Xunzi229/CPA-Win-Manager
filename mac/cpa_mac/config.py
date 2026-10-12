"""Mac manager defaults and version comparison."""
from pathlib import Path
import json
import os
import platform
import re
import sys


def get_mac_root():
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        if exe_path.parent.name == "MacOS" and exe_path.parents[1].name == "Contents":
            return exe_path.parents[2]
        return exe_path.parent
    return Path(__file__).resolve().parents[1]


MAC_ROOT = get_mac_root()
_raw_version = os.environ.get("RELEASE_TAG") or os.environ.get("GITHUB_REF_NAME") or "1.5.14"
if _raw_version in ("main", "master") or not _raw_version:
    _raw_version = "1.5.14"
SOURCE_VERSION = _raw_version.removeprefix("v")
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _version_file = Path(sys._MEIPASS) / "app-version.json"
    if _version_file.is_file():
        SOURCE_VERSION = json.loads(_version_file.read_text(encoding="utf-8"))["version"]

PROJECTS = {
    "cli": {
        "key": "cli",
        "title": "CLIProxyAPI",
        "repo": "https://github.com/router-for-me/CLIProxyAPI",
        "binary": "cli-proxy-api",
        "config": "config.yaml",
        "version_args": ["--help"],
        "version_pattern": r"CLIProxyAPI Version:\s*([^,\s]+)",
        "start_args": ["--config", "config.yaml"],
        "arch_token": {"arm64": "aarch64", "amd64": "amd64"},
        "asset_template": r"CLIProxyAPI_.+_darwin_{arch}\.tar\.gz$",
    },
    "plus": {
        "key": "plus",
        "title": "CPA-Manager-Plus",
        "repo": "https://github.com/seakee/CPA-Manager-Plus",
        "binary": "cpa-manager-plus",
        "config": "config.json",
        "version_args": ["--version"],
        "version_pattern": r"v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?",
        "start_args": [],
        "arch_token": {"arm64": "arm64", "amd64": "amd64"},
        "asset_template": r"cpa-manager-plus_.+_darwin_{arch}\.tar\.gz$",
    },
}

CATALOG_CATEGORIES = [
    "全部",
    "macOS / 系统增强",
    "文件管理 / 搜索",
    "Git / 开发",
    "终端工具",
    "API / 数据库开发",
    "Docker / 容器",
    "服务器监控",
    "AI 工具",
    "密码 / 安全",
]

CATALOG = [
    # macOS / 系统增强
    ("安装包", "macOS / 系统增强", "Raycast", "macOS 极速聚焦启动器与工作流", "https://github.com/raycast/script-commands"),
    ("安装包", "macOS / 系统增强", "Stats", "macOS 菜单栏系统资源监控", "https://github.com/exelban/stats"),
    ("安装包", "macOS / 系统增强", "Maccy", "轻量剪贴板历史管理", "https://github.com/p0deje/Maccy"),
    ("安装包", "macOS / 系统增强", "AltTab", "macOS 窗口切换器 (Windows Alt-Tab 体验)", "https://github.com/lwouis/alt-tab-macos"),
    ("安装包", "macOS / 系统增强", "MonitorControl", "外接显示器亮度和音量控制", "https://github.com/MonitorControl/MonitorControl"),
    ("安装包", "macOS / 系统增强", "Rectangle", "窗口快捷分屏与移动", "https://github.com/rxhanson/Rectangle"),
    ("安装包", "macOS / 系统增强", "AppCleaner", "macOS 应用程序彻底卸载", "https://github.com/alienator88/AppCleaner"),
    ("安装包", "macOS / 系统增强", "LocalSend", "跨平台局域网免配对传输工具", "https://github.com/localsend/localsend"),
    ("安装包", "macOS / 系统增强", "Clash Verge Rev", "现代代理客户端", "https://github.com/clash-verge-rev/clash-verge-rev"),

    # 文件管理 / 搜索
    ("免安装", "文件管理 / 搜索", "fd", "快速高效的 find 替代工具", "https://github.com/sharkdp/fd"),
    ("免安装", "文件管理 / 搜索", "ripgrep", "高速文本正则搜索工具 (rg)", "https://github.com/BurntSushi/ripgrep"),
    ("免安装", "文件管理 / 搜索", "fzf", "通用命令行模糊查找器", "https://github.com/junegunn/fzf"),
    ("免安装", "文件管理 / 搜索", "duf", "直观磁盘使用量查看工具", "https://github.com/muesli/duf"),
    ("免安装", "文件管理 / 搜索", "dust", "直观磁盘占用图形分析器", "https://github.com/bootandy/dust"),
    ("免安装", "文件管理 / 搜索", "czkawka", "重复文件、大文件清理工具", "https://github.com/qarmin/czkawka"),

    # Git / 开发效率
    ("免安装", "Git / 开发", "lazygit", "终端全键盘 Git 界面", "https://github.com/jesseduffield/lazygit"),
    ("免安装", "Git / 开发", "gitui", "极速 Rust 终端 Git 客户端", "https://github.com/gitui-org/gitui"),
    ("免安装", "Git / 开发", "delta", "带有语法高亮的 Git Diff 查看器", "https://github.com/dandavison/delta"),
    ("免安装", "Git / 开发", "gh", "GitHub 官方命令行管理工具", "https://github.com/cli/cli"),
    ("免安装", "Git / 开发", "jq", "命令行 JSON 数据处理器", "https://github.com/jqlang/jq"),
    ("免安装", "Git / 开发", "yq", "命令行 YAML 数据处理器", "https://github.com/mikefarah/yq"),

    # 终端工具
    ("安装包", "终端工具", "WezTerm", "GPU 加速多标签分屏高性能终端", "https://github.com/wezterm/wezterm"),
    ("安装包", "终端工具", "Alacritty", "极简高性能 GPU 渲染终端", "https://github.com/alacritty/alacritty"),
    ("安装包", "终端工具", "Ghostty", "现代极速 GPU 渲染终端", "https://github.com/ghostty-org/ghostty"),
    ("安装包", "终端工具", "Tabby", "跨平台现代 SSH / 终端客户端", "https://github.com/Eugeny/tabby"),
    ("免安装", "终端工具", "bat", "带语法高亮与 Git 集成的 cat 替代品", "https://github.com/sharkdp/bat"),
    ("免安装", "终端工具", "eza", "现代带颜色图标的 ls 替代工具", "https://github.com/eza-community/eza"),
    ("免安装", "终端工具", "zoxide", "根据使用习惯自动学习跳转目录", "https://github.com/ajeetdsouza/zoxide"),
    ("免安装", "终端工具", "starship", "超快轻量无依赖跨 Shell 提示符", "https://github.com/starship/starship"),
    ("免安装", "终端工具", "btop", "精美交互式终端系统资源监控", "https://github.com/aristocratos/btop"),
    ("免安装", "终端工具", "bottom", "定制化跨平台终端进程与负载监控", "https://github.com/ClementTsang/bottom"),
    ("免安装", "终端工具", "hyperfine", "跨平台基准性能命令行测试工具", "https://github.com/sharkdp/hyperfine"),

    # API / 数据库开发
    ("安装包", "API / 数据库开发", "Bruno", "开源轻量 API 调试工具 (Postman 替代品)", "https://github.com/usebruno/bruno"),
    ("安装包", "API / 数据库开发", "DBeaver", "万能数据库管理与 SQL 查询客户端", "https://github.com/dbeaver/dbeaver"),
    ("安装包", "API / 数据库开发", "Beekeeper Studio", "美观易用的 SQL 编辑器与数据库管理器", "https://github.com/beekeeper-studio/beekeeper-studio"),
    ("安装包", "API / 数据库开发", "Yaak", "现代高性能 API 调试客户端", "https://github.com/mountain-loop/yaak"),
    ("免安装", "API / 数据库开发", "httpie", "人性化现代命令行 HTTP 请求工具", "https://github.com/httpie/cli"),

    # Docker / 容器
    ("免安装", "Docker / 容器", "lazydocker", "终端一键管理 Docker 容器与镜像", "https://github.com/jesseduffield/lazydocker"),
    ("免安装", "Docker / 容器", "dive", "逐层分析与缩减 Docker 镜像体积", "https://github.com/wagoodman/dive"),
    ("免安装", "Docker / 容器", "k9s", "全键盘极速 Kubernetes 集群控制终端", "https://github.com/derailed/k9s"),

    # 服务器监控
    ("安装包", "服务器监控", "Beszel", "轻量级服务器与多主机仪表盘", "https://github.com/henrygd/beszel"),
    ("安装包", "服务器监控", "OBS Studio", "高性能屏幕录制与直播推流工具", "https://github.com/obsproject/obs-studio"),

    # AI 工具
    ("安装包", "AI 工具", "Ollama", "本地运行大型语言模型 (Llama/Qwen 等)", "https://github.com/ollama/ollama"),
    ("免安装", "AI 工具", "llama.cpp", "纯 C/C++ 高性能本地大模型推理引擎", "https://github.com/ggml-org/llama.cpp"),
    ("安装包", "AI 工具", "AnythingLLM", "本地私有知识库与 RAG 对话客户端", "https://github.com/Mintplex-Labs/anything-llm"),

    # 密码 / 安全
    ("安装包", "密码 / 安全", "KeePassXC", "离线高安全密码管理器", "https://github.com/keepassxreboot/keepassxc"),
    ("安装包", "密码 / 安全", "Bitwarden", "跨平台密码保险库客户端", "https://github.com/bitwarden/clients"),
    ("安装包", "密码 / 安全", "Cryptomator", "客户端透明网盘文件加密工具", "https://github.com/cryptomator/cryptomator"),
    ("免安装", "密码 / 安全", "age", "现代化简单安全的文件加密工具", "https://github.com/FiloSottile/age"),
]


def host_architecture():
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "amd64"
    raise RuntimeError(f"不支持的 Mac 架构：{machine or '未知'}。")


def version_key(version):
    if not isinstance(version, str):
        return None
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", version.strip())
    if not match:
        return None
    prerelease = match[4]
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in prerelease.split(".")) if prerelease else ()
    return tuple(int(match[i]) for i in (1, 2, 3)), prerelease is None, identifiers


def has_update(local, latest):
    local_key = version_key(local) if local else None
    latest_key = version_key(latest) if latest else None
    return local_key is not None and latest_key is not None and latest_key > local_key


def already_current(local, latest):
    local_key = version_key(local) if local else None
    latest_key = version_key(latest) if latest else None
    return local_key is not None and latest_key is not None and local_key >= latest_key

DEFAULT_DOWNLOAD_WORKERS = 4
MIN_DOWNLOAD_WORKERS = 1
MAX_DOWNLOAD_WORKERS = 16


def normalize_download_workers(value):
    try:
        val = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DOWNLOAD_WORKERS
    if val < MIN_DOWNLOAD_WORKERS:
        return MIN_DOWNLOAD_WORKERS
    if val > MAX_DOWNLOAD_WORKERS:
        return MAX_DOWNLOAD_WORKERS
    return val
