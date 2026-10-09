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


def install_package(release, asset, download_dir, proxy, report, cancel=None, workers=4):
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
        temporary = folder / (".partial-" + filename)
        try:
            github.download_verified(release, asset, temporary, proxy, report, cancel, workers=workers)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    report(95, "正在打开安装包…")
    result = subprocess.run(["open", str(destination)], capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError("无法打开安装包" + (f"：{detail}" if detail else "。"))
    report(100, f"已打开 {filename}。")
    return destination
