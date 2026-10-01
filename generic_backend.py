"""GitHub release discovery and transactional portable installation."""
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import tempfile
import urllib.parse
import uuid
import zipfile

import cli_backend
import resumable_download as transfer
from runtime_utils import windows_architecture

METADATA = ".github-install.json"


def repository(value):
    parsed = urllib.parse.urlsplit(value.strip())
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2])):
        raise ValueError("请填写 https://github.com/作者/仓库 或其 Releases 地址。")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if owner in (".", "..") or repo in ("", ".", ".."):
        raise ValueError("GitHub 仓库地址无效。")
    return f"https://github.com/{owner}/{repo}"


def releases(value, proxy):
    repo = repository(value)
    opener = cli_backend.network(proxy)
    data = json.loads(cli_backend.read_text(opener, "https://api.github.com/repos/" +
                                           repo.split("github.com/")[1] + "/releases/latest"))
    assets = []
    for item in data.get("assets", []):
        name, url = item.get("name", ""), item.get("browser_download_url", "")
        if name and url.startswith(repo + "/releases/download/"):
            assets.append({"name": name, "url": url, "digest": item.get("digest") or "",
                           "size": item.get("size"), "id": item.get("id"),
                           "updated_at": item.get("updated_at")})
    if not data.get("tag_name") or not assets:
        raise RuntimeError("此仓库的最新正式 Release 没有可下载附件。")
    return {"repository": repo, "tag": data["tag_name"], "assets": assets,
            "notes": data.get("body") or "暂无更新说明。"}


def candidates(release, mode="便携安装"):
    suffixes = (".exe", ".msi") if mode == "安装器" else (".zip", ".exe")
    arch = windows_architecture()
    def score(asset):
        name = asset["name"].lower()
        own = ("arm64", "aarch64") if arch == "arm64" else ("amd64", "x64", "x86_64")
        other = ("amd64", "x64", "x86_64") if arch == "arm64" else ("arm64", "aarch64")
        return (any(p in name for p in ("linux", "darwin", "macos")),
                any(p in name for p in other), -int("win" in name),
                -int(any(p in name for p in own)), name)
    return sorted([a for a in release["assets"] if a["name"].lower().endswith(suffixes)], key=score)


def safe_relative(value):
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(p in ("", ".", "..") or any(c in p for c in ':*?<>|"')
                   or any(ord(c) < 32 for c in p) or p.endswith((".", " "))
                   or cli_backend.reserved_name(p) for p in parts)):
        raise ValueError("无效的相对路径：" + value)
    return Path(*parts)


def extract(archive, stage):
    seen, total = set(), 0
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 20000:
            raise RuntimeError("安装包文件数量超过限制。")
        for entry in package.infolist():
            relative = safe_relative(entry.filename.rstrip("/"))
            key = relative.as_posix().lower()
            total += entry.file_size
            if key in seen or total > 2 * 1024**3 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("安装包包含重复路径、链接或解压体积超过 2 GB。")
            seen.add(key)
            target = stage / relative
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
    # Remove a sole enclosing directory, retaining all package contents.
    content = stage
    while True:
        children = list(content.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            return content
        content = children[0]


def asset_identity(asset):
    return json.dumps([asset.get("id"), asset.get("updated_at"), asset.get("digest"),
                       asset.get("size")]) if asset.get("id") or asset.get("digest") else ""


def download(release, asset, destination, proxy, report, cancel=None):
    if asset not in release["assets"]:
        raise ValueError("附件不属于当前 Release。")
    opener = cli_backend.network(proxy)
    identity = asset_identity(asset)
    digest = transfer.fetch(asset["url"], destination, report, proxy=proxy,
                            cancel=cancel, identity=identity)
    transfer.check_cancel(cancel)
    expected = None
    if asset.get("digest"):
        match = re.fullmatch(r"sha256:([a-fA-F0-9]{64})", asset["digest"])
        if match:
            expected = match[1]
    if expected is None:
        sums = [a for a in release["assets"] if a["name"].lower() in
                ("checksums.txt", "sha256sums.txt", "sha256sums", "sha256sum.txt")]
        if len(sums) == 1:
            text = cli_backend.read_text(opener, sums[0]["url"])
            values = re.findall(r"^([a-fA-F0-9]{64})\s+\*?(?:\./)?" +
                                re.escape(asset["name"]) + r"\s*$", text, re.MULTILINE)
            if len(values) != 1:
                raise RuntimeError("校验文件缺少所选附件的唯一 SHA256。")
            expected = values[0]
    if expected and digest.lower() != expected.lower():
        transfer.discard(asset["url"], identity)
        raise RuntimeError("SHA256 校验失败，已取消安装。")
    transfer.check_cancel(cancel)
    report(75, "SHA256 校验通过。" if expected else "发布者未提供可识别的 SHA256，已完成下载。")


def install(release, asset, directory, preserve, proxy, report, cancel=None):
    root = Path(directory).expanduser().resolve()
    if root == Path(root.anchor) or root == cli_backend.ROOT.resolve():
        raise ValueError("请使用独立的软件目录，不能安装到磁盘根目录或管理器目录。")
    protected = [safe_relative(p.strip()).as_posix().lower() for p in preserve.split(";") if p.strip()]
    if METADATA in protected or "update.lock" in protected:
        raise ValueError("保留路径不能包含管理器的安装记录或锁文件。")
    root.mkdir(parents=True, exist_ok=True)
    # Serialize with the existing managers too.
    with cli_backend.update_lock(root), tempfile.TemporaryDirectory(prefix="github-package-") as temporary:
        work = Path(temporary)
        archive, stage = work / "download", work / "payload"
        stage.mkdir()
        download(release, asset, archive, proxy, report, cancel)
        transfer.check_cancel(cancel)
        report(80, "下载完成，正在准备安装。")
        if asset["name"].lower().endswith(".zip"):
            content = extract(archive, stage)
        elif asset["name"].lower().endswith(".exe"):
            filename = safe_relative(asset["name"])
            if len(filename.parts) != 1:
                raise ValueError("EXE 附件名称不能包含目录。")
            shutil.copyfile(archive, stage / filename)
            content = stage
        else:
            raise ValueError("便携安装只支持 ZIP 和单个 EXE。")
        files = [p for p in content.rglob("*") if p.is_file()]
        if not files:
            raise RuntimeError("安装包为空。")
        if any(p.relative_to(content).parts[0].lower() in (METADATA, "update.lock")
               or p.relative_to(content).parts[0].lower().startswith(".install-") for p in files):
            raise RuntimeError("安装包包含管理器保留文件名。")
        # Metadata participates in the same transaction as the program files.
        (content / METADATA).write_text(json.dumps({"repository": release["repository"],
            "version": release["tag"], "asset": asset["name"]}, ensure_ascii=False), encoding="utf-8")
        files.append(content / METADATA)
        transfer.check_cancel(cancel)
        backup = root / (".install-backup-" + uuid.uuid4().hex)
        backup.mkdir()
        changed, created_dirs = [], []
        try:
            for source in files:
                relative = source.relative_to(content)
                target = root / relative
                if not target.resolve().is_relative_to(root):
                    raise RuntimeError("安装路径通过链接指向软件目录外。")
                cursor = target
                while cursor != root:
                    if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                        raise RuntimeError("安装目标包含链接路径：" + str(relative))
                    cursor = cursor.parent
                key = relative.as_posix().lower()
                if target.exists() and any(key == p or key.startswith(p + "/") for p in protected):
                    continue
                existed = target.exists()
                if existed:
                    if not target.is_file():
                        raise RuntimeError("安装文件与已有目录冲突：" + str(relative))
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                missing = []
                parent = target.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                target.parent.mkdir(parents=True, exist_ok=True)
                created_dirs.extend(reversed(missing))
                # Replace atomically so a locked executable does not get truncated.
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".install-", delete=False) as pending:
                    pending_path = Path(pending.name)
                try:
                    shutil.copy2(source, pending_path)
                    os.replace(pending_path, target)
                    changed.append((relative, existed))
                finally:
                    pending_path.unlink(missing_ok=True)
            report(100, "安装完成：" + release["tag"] + "；备份：" + str(backup))
        except Exception as error:
            failures = []
            for relative, existed in reversed(changed):
                try:
                    if existed:
                        os.replace(backup / relative, root / relative)
                    else:
                        (root / relative).unlink(missing_ok=True)
                except OSError:
                    failures.append(str(relative))
            for folder in reversed(created_dirs):
                try:
                    folder.rmdir()
                except OSError:
                    pass
            if failures:
                raise RuntimeError("安装失败，部分文件恢复失败：" + ", ".join(failures) +
                                   "；备份位于 " + str(backup)) from error
            raise RuntimeError("安装失败，已恢复修改的文件。请先关闭目标软件再重试。原因：" + str(error)) from error


def local_version(directory, repo):
    try:
        data = json.loads((Path(directory) / METADATA).read_text(encoding="utf-8"))
        return data.get("version") if data.get("repository") == repository(repo) else None
    except (OSError, ValueError, AttributeError):
        return None


def download_installer(release, asset, directory, proxy, report, cancel=None):
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = safe_relative(asset["name"])
    if len(filename.parts) != 1 or filename.suffix.lower() not in (".exe", ".msi"):
        raise ValueError("安装器必须是 EXE 或 MSI 附件。")
    with tempfile.TemporaryDirectory(prefix="github-installer-") as temporary:
        source = Path(temporary) / "installer"
        download(release, asset, source, proxy, report, cancel)
        transfer.check_cancel(cancel)
        report(80, "下载完成，正在保存安装器。")
        # Never overwrite an existing executable in the download folder.
        output = root / ("installer-" + uuid.uuid4().hex[:8] + "-" + filename.name)
        shutil.copy2(source, output)
    return output
