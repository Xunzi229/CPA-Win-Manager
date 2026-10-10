"""Mac manager defaults and version comparison."""
from pathlib import Path
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

CATALOG = [
    ("免安装", "fd", "find 替代工具", "https://github.com/sharkdp/fd"),
    ("免安装", "ripgrep", "文本搜索", "https://github.com/BurntSushi/ripgrep"),
    ("免安装", "bat", "带高亮的 cat", "https://github.com/sharkdp/bat"),
    ("免安装", "fzf", "模糊搜索", "https://github.com/junegunn/fzf"),
    ("免安装", "jq", "命令行 JSON 处理", "https://github.com/jqlang/jq"),
    ("免安装", "yq", "命令行 YAML 处理", "https://github.com/mikefarah/yq"),
    ("免安装", "lazygit", "Git 终端界面", "https://github.com/jesseduffield/lazygit"),
    ("免安装", "gitui", "终端 Git 客户端", "https://github.com/gitui-org/gitui"),
    ("免安装", "delta", "Git diff 美化", "https://github.com/dandavison/delta"),
    ("免安装", "gh", "命令行管理 GitHub", "https://github.com/cli/cli"),
    ("免安装", "lazydocker", "Docker 终端界面", "https://github.com/jesseduffield/lazydocker"),
    ("免安装", "dive", "分析 Docker 镜像层", "https://github.com/wagoodman/dive"),
    ("免安装", "k9s", "Kubernetes 终端界面", "https://github.com/derailed/k9s"),
    ("免安装", "starship", "跨 Shell 提示符", "https://github.com/starship/starship"),
    ("免安装", "eza", "ls 替代工具", "https://github.com/eza-community/eza"),
    ("免安装", "zoxide", "智能目录跳转", "https://github.com/ajeetdsouza/zoxide"),
    ("免安装", "atuin", "Shell 历史搜索", "https://github.com/atuinsh/atuin"),
    ("免安装", "btop", "系统监控", "https://github.com/aristocratos/btop"),
    ("免安装", "bottom", "系统监控", "https://github.com/ClementTsang/bottom"),
    ("免安装", "duf", "磁盘空间查看", "https://github.com/muesli/duf"),
    ("免安装", "dust", "磁盘占用分析", "https://github.com/bootandy/dust"),
    ("免安装", "hyperfine", "命令性能测试", "https://github.com/sharkdp/hyperfine"),
    ("免安装", "helix", "终端代码编辑器", "https://github.com/helix-editor/helix"),
    ("免安装", "neovim", "终端代码编辑器", "https://github.com/neovim/neovim"),
    ("免安装", "age", "文件加密", "https://github.com/FiloSottile/age"),
    ("安装包", "Ollama", "本地大模型", "https://github.com/ollama/ollama"),
    ("安装包", "KeePassXC", "本地密码管理", "https://github.com/keepassxreboot/keepassxc"),
    ("安装包", "Alacritty", "终端", "https://github.com/alacritty/alacritty"),
    ("安装包", "WezTerm", "终端", "https://github.com/wezterm/wezterm"),
    ("安装包", "Ghostty", "终端", "https://github.com/ghostty-org/ghostty"),
    ("安装包", "Tabby", "终端", "https://github.com/Eugeny/tabby"),
    ("安装包", "OBS Studio", "录屏", "https://github.com/obsproject/obs-studio"),
    ("安装包", "Bruno", "API 调试", "https://github.com/usebruno/bruno"),
    ("安装包", "DBeaver", "数据库客户端", "https://github.com/dbeaver/dbeaver"),
    ("安装包", "Bitwarden", "密码管理", "https://github.com/bitwarden/clients"),
    ("安装包", "Cryptomator", "云盘文件加密", "https://github.com/cryptomator/cryptomator"),
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
