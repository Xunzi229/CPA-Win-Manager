"""Safe extraction for zip and tar archives."""
from pathlib import Path
import os
import stat
import tarfile
import zipfile


MAX_FILES = 20000
MAX_BYTES = 2 * 1024 ** 3
DOC_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".html", ".pdf", ".1", ".rst"}


def archive_kind(name):
    lower = name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")):
        return "tar"
    return None


def _parts(name):
    text = name.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    parts = text.split("/") if text else []
    if (not parts or any(part in ("", ".", "..") for part in parts)
            or name.startswith("/") or (len(name) > 1 and name[1] == ":")):
        raise RuntimeError("压缩包包含不安全的路径：" + name)
    return parts


def _guard(stage, parts):
    root = stage.resolve()
    target = root.joinpath(*parts)
    if not target.is_relative_to(root):
        raise RuntimeError("压缩包包含不安全的路径：" + "/".join(parts))
    return target


def _account(count, total, size):
    count += 1
    total += size
    if count > MAX_FILES or total > MAX_BYTES:
        raise RuntimeError("安装包文件数量或解压体积超过限制。")
    return count, total


def extract_archive(archive, stage, name):
    stage.mkdir(parents=True, exist_ok=True)
    kind = archive_kind(name)
    if kind == "zip":
        _extract_zip(archive, stage)
    elif kind == "tar":
        _extract_tar(archive, stage)
    else:
        raise ValueError("只支持 zip、tar.gz、tar.xz 和 tar.bz2。")
    return unwrap(stage)


def unwrap(stage):
    content = stage
    while True:
        children = [path for path in content.iterdir() if path.name not in ("__MACOSX", ".DS_Store")]
        if len(children) == 1 and children[0].is_dir() and not children[0].is_symlink():
            content = children[0]
            continue
        return content


def mark_executables(root):
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    for path in files:
        if path.stat().st_mode & 0o111:
            os.chmod(path, 0o755)
    documents = [path for path in files if path.suffix.lower() in DOC_SUFFIXES]
    if len(files) == 1 and files[0] not in documents:
        os.chmod(files[0], 0o755)


def _extract_zip(archive, stage):
    count = total = 0
    seen = set()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            parts = _parts(info.filename)
            key = "/".join(parts).lower()
            if key in seen:
                raise RuntimeError("压缩包包含重复路径：" + info.filename)
            seen.add(key)
            unix = info.external_attr >> 16
            if stat.S_IFMT(unix) == stat.S_IFLNK:
                raise RuntimeError("压缩包包含链接：" + info.filename)
            target = _guard(stage, parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            count, total = _account(count, total, info.file_size)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open("wb") as output:
                while chunk := source.read(256 * 1024):
                    output.write(chunk)
            mode = unix & 0o777
            if mode & 0o111:
                os.chmod(target, 0o755)
            else:
                os.chmod(target, 0o644)


def _extract_tar(archive, stage):
    count = total = 0
    seen = set()
    with tarfile.open(archive, "r:*") as package:
        for entry in package:
            parts = _parts(entry.name)
            key = "/".join(parts).lower()
            if key in seen:
                raise RuntimeError("压缩包包含重复路径：" + entry.name)
            seen.add(key)
            if entry.issym() or entry.islnk():
                raise RuntimeError("压缩包包含链接：" + entry.name)
            target = _guard(stage, parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not entry.isfile():
                raise RuntimeError("压缩包包含不支持的文件类型：" + entry.name)
            count, total = _account(count, total, entry.size)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = package.extractfile(entry)
            if source is None:
                raise RuntimeError("无法读取压缩包文件：" + entry.name)
            with source, target.open("wb") as output:
                while chunk := source.read(256 * 1024):
                    output.write(chunk)
            if entry.mode & 0o111:
                os.chmod(target, 0o755)
            else:
                os.chmod(target, 0o644)
