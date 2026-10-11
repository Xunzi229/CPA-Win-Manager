"""Portable archives and dmg/pkg packages from GitHub releases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from cpa_mac.backends import github
from cpa_mac.backends.service import assert_install_directory
from cpa_mac.core.archive import archive_kind, extract_archive, mark_executables
from cpa_mac.core.lock import file_lock
from cpa_mac.core.macos import (
    install_dmg_app,
    link_to_user_bin,
    open_in_terminal,
    remove_quarantine,
    reveal_in_finder,
    notify,
)


METADATA = ".cpa-mac-install.json"


def parse_preserve(value):
    items = []
    for part in (value or "").replace("；", ";").split(";"):
        text = part.strip().replace("\\", "/").strip("/")
        if not text:
            continue
        pieces = text.split("/")
        if any(piece in ("", ".", "..") for piece in pieces):
            raise ValueError("保留路径无效：" + text)
        items.append(text.lower())
    if METADATA.lower() in items:
        raise ValueError("保留路径不能包含安装记录。")
    return items


def installed_tag(directory):
    path = Path(directory) / METADATA
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    tag = data.get("tag") if isinstance(data, dict) else ""
    return tag if isinstance(tag, str) else ""


def _copy_tree(content, root, preserve):
    files = [path for path in content.rglob("*") if path.is_file() and not path.is_symlink()]
    if not files:
        raise RuntimeError("安装包为空。")
    copied = []
    for path in files:
        relative = path.relative_to(content)
        if relative.as_posix().lower() in preserve or relative.parts[0].lower() == METADATA:
            continue
        if any(part == "__MACOSX" or part == ".DS_Store" for part in relative.parts):
            continue
        target = root / relative
        if target.is_symlink() or (target.exists() and not target.resolve().is_relative_to(root.resolve())):
            raise RuntimeError("目标文件指向安装目录之外：" + relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(target)
    if not copied:
        raise RuntimeError("安装包没有可写入的文件。")
    mark_executables(root)


def install_portable(release, asset, directory, proxy, report, source_root, preserve="", cancel=None, workers=4):
    root = assert_install_directory(directory, source_root)
    protected = parse_preserve(preserve)
    root.mkdir(parents=True, exist_ok=True)
    with file_lock(root / "update.lock"), tempfile.TemporaryDirectory(prefix="cpa-mac-app-") as temporary:
        work = Path(temporary)
        blob = work / "download"
        github.download_verified(release, asset, blob, proxy, report, cancel, workers=workers)
        report(80, "下载完成，正在准备安装。")
        if archive_kind(asset["name"]):
            content = extract_archive(blob, work / "stage", asset["name"])
            _copy_tree(content, root, protected)
        else:
            filename = Path(asset["name"]).name
            if filename != asset["name"] or filename in ("", ".", ".."):
                raise ValueError("单文件附件名称不能包含目录。")
            target = root / filename
            shutil.copyfile(blob, target)
            os.chmod(target, 0o755)
        record = {"repository": release["repository"], "tag": release["tag"], "asset": asset["name"],
                  "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        path = root / METADATA
        temporary_record = root / (".install-" + os.urandom(4).hex() + ".tmp")
        try:
            temporary_record.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary_record, path)
        finally:
            temporary_record.unlink(missing_ok=True)
    report(100, f"安装完成：{release['tag']}。")
    return release["tag"]


def _reusable(path, asset, expected):
    if not path.is_file():
        return False
    if expected:
        return github.file_sha256(path).lower() == expected.lower()
    size = asset.get("size")
    return isinstance(size, int) and size >= 0 and path.stat().st_size == size


def install_package(release, asset, download_dir, proxy, report, cancel=None, workers=4, open_installer=True, downloaded=None):
    filename = Path(asset["name"]).name
    if filename != asset["name"] or filename in ("", ".", ".."):
        raise ValueError("安装包名称不能包含目录。")
    folder = Path(download_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / filename
    opener = github.network(proxy, accept="*/*")
    expected = github.expected_sha256(release, asset, opener)
    if _reusable(destination, asset, expected):
        report(90, "已复用下载目录中的安装包。")
    else:
        handle = tempfile.NamedTemporaryFile(prefix=".partial-", dir=folder, delete=False)
        temporary = Path(handle.name)
        handle.close()
        try:
            github.download_verified(release, asset, temporary, proxy, report, cancel, workers=workers)
            os.replace(temporary, destination)
            if downloaded:
                downloaded(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if not open_installer:
        report(100, f"下载完成：{filename}。")
        return destination
    report(95, "正在打开安装包…")
    result = subprocess.run(["open", str(destination)], capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError("无法打开安装包" + (f"：{detail}" if detail else "。"))
    report(100, f"已打开 {filename}。")
    return destination


# ==============================================================================
# 免安装软件运行、卸载与安装包清理 (参考 Windows Portable/Installer 实现)
# ==============================================================================

def launch_software(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("软件安装目录不存在，请先安装。")

    # 1. 查找是否存在 .app 应用程序包
    apps = [p for p in root.glob("*.app") if p.is_dir()]
    if not apps:
        apps = [p for p in root.glob("**/*.app") if p.is_dir() and "__MACOSX" not in p.parts]
    if apps:
        app_path = apps[0]
        res = subprocess.run(["open", str(app_path)], capture_output=True, text=True)
        if res.returncode:
            raise RuntimeError(f"无法启动应用 {app_path.name}：{res.stderr or res.stdout}")
        return f"已启动应用程序：{app_path.name}"

    # 2. 查找是否有可执行二进制文件
    binaries = []
    for path in root.glob("*"):
        if path.is_file() and not path.name.startswith(".") and os.access(path, os.X_OK):
            binaries.append(path)
    if not binaries:
        for path in root.glob("**/*"):
            if path.is_file() and not path.name.startswith(".") and "__MACOSX" not in path.parts and os.access(path, os.X_OK):
                binaries.append(path)
                break

    if binaries:
        target = binaries[0]
        # 尝试在 macOS Terminal 中直接运行
        script = f'tell application "Terminal" to do script "{target.resolve()}"'
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if res.returncode == 0:
            return f"已在终端中启动：{target.name}"
        # 回退：在访达中高亮定位
        subprocess.run(["open", "-R", str(target)], check=False)
        return f"已在访达中定位：{target.name}"

    subprocess.run(["open", str(root)], check=False)
    return f"已打开目录：{root.name}"


def uninstall_portable(directory, source_root):
    root = Path(directory).expanduser().resolve()
    source = Path(source_root).resolve()
    home = Path.home().resolve()
    if root in (Path(root.anchor), home, source) or source in root.parents:
        raise ValueError("安全保护：不能删除系统根目录、家目录或管理器所在目录。")
    if not root.exists():
        return
    shutil.rmtree(root)


def delete_package_file(download_dir, asset_name):
    if not asset_name:
        return False
    filename = Path(asset_name).name
    if filename != asset_name or filename in ("", ".", ".."):
        return False
    target = Path(download_dir).expanduser().resolve() / filename
    if target.is_file():
        target.unlink(missing_ok=True)
        return True
    return False


def clear_portable_backups(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return 0
    import re
    deleted = 0
    for p in root.iterdir():
        if p.is_dir() and re.fullmatch(r"\.install-backup-[0-9a-f]{32}", p.name):
            try:
                shutil.rmtree(p)
                deleted += 1
            except OSError:
                pass
    return deleted


# ==============================================================================
# macOS 专属能力：DMG 自动提取安装、CLI 终端软链、访达联动
# ==============================================================================

def install_dmg_to_applications(download_dir, asset_name, target_dir="/Applications", report=None):
    folder = Path(download_dir).expanduser().resolve()
    dmg_file = folder / asset_name
    if not dmg_file.is_file():
        raise FileNotFoundError(f"在下载目录中未找到镜像文件: {asset_name}")
    app_path = install_dmg_app(dmg_file, target_dir=target_dir, report=report)
    notify("macOS 应用安装完成", f"{app_path.name} 已安装至 {target_dir}")
    return str(app_path)


def create_cli_symlink(directory, link_name=""):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("软件安装目录不存在。")
    # 查找主要可执行文件
    binaries = []
    for path in root.glob("*"):
        if path.is_file() and not path.name.startswith(".") and os.access(path, os.X_OK):
            binaries.append(path)
    if not binaries:
        for path in root.glob("**/*"):
            if path.is_file() and not path.name.startswith(".") and "__MACOSX" not in path.parts and os.access(path, os.X_OK):
                binaries.append(path)
                break
    if not binaries:
        raise RuntimeError("在目录中未找到可执行的命令行程序。")
    target_bin = binaries[0]
    remove_quarantine(target_bin)
    link = link_to_user_bin(target_bin, link_name=link_name or target_bin.name)
    notify("终端命令就绪", f"已创建软链接：{link.name} -> {target_bin.name}")
    return str(link)


def open_software_terminal(directory):
    return open_in_terminal(directory)


def reveal_software_in_finder(path):
    return reveal_in_finder(path)
