"""Transactional ZIP and single executable portable installation."""
import inspect
import json
import os


def _accepts_workers(func):
    fn = getattr(func, "side_effect", None) or func
    if not callable(fn):
        return False
    try:
        sig = inspect.signature(fn)
        return "workers" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except (ValueError, TypeError):
        return False
from pathlib import Path
import shutil
import tempfile
import uuid
import zipfile

from cpa_manager.backends import github
from cpa_manager.core.files import safe_relative
from cpa_manager.core.paths import ROOT
from cpa_manager.core.locking import update_lock
from cpa_manager.core import download as transfer

METADATA = ".github-install.json"


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


def install(release, asset, directory, preserve, proxy, report, cancel=None, workers=None):
    root = Path(directory).expanduser().resolve()
    if root == Path(root.anchor) or root == ROOT.resolve():
        raise ValueError("请使用独立的软件目录，不能安装到磁盘根目录或管理器目录。")
    protected = [safe_relative(p.strip()).as_posix().lower() for p in preserve.split(";") if p.strip()]
    if METADATA in protected or "update.lock" in protected:
        raise ValueError("保留路径不能包含管理器的安装记录或锁文件。")
    root.mkdir(parents=True, exist_ok=True)
    # Serialize with the existing managers too.
    with update_lock(root), tempfile.TemporaryDirectory(prefix="github-package-") as temporary:
        work = Path(temporary)
        archive, stage = work / "download", work / "payload"
        stage.mkdir()
        if workers is not None and _accepts_workers(github.download):
            github.download(release, asset, archive, proxy, report, cancel, workers=workers)
        else:
            github.download(release, asset, archive, proxy, report, cancel)
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
        try:
            previous = json.loads((root / METADATA).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        records = previous.get("software", {})
        records = dict(records) if isinstance(records, dict) else {}
        if isinstance(previous.get("repository"), str):
            try:
                records.setdefault(github.repository(previous["repository"]).lower(),
                                   {k: previous.get(k) for k in ("repository", "version", "asset")})
            except ValueError:
                pass
        installed = {"repository": release["repository"], "version": release["tag"], "asset": asset["name"]}
        records[github.repository(release["repository"]).lower()] = installed
        (content / METADATA).write_text(json.dumps(dict(installed, software=records), ensure_ascii=False), encoding="utf-8")
        files.append(content / METADATA)
        transfer.check_cancel(cancel)
        backup = root / (".install-backup-" + uuid.uuid4().hex)
        if isinstance(cancel, transfer.DownloadControl):
            cancel.begin_commit()
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
        key = github.repository(repo).lower()
        records = data.get("software", {})
        record = records.get(key) if isinstance(records, dict) else None
        if isinstance(record, dict):
            return record.get("version")
        return data.get("version") if github.repository(data.get("repository", "")).lower() == key else None
    except (OSError, ValueError, AttributeError):
        return None
