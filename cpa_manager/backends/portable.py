"""Transactional ZIP and single executable portable installation."""
import hashlib
import inspect
import json
import os
import re
import time


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
from bisect import bisect_left

from cpa_manager.backends import github
from cpa_manager.core.files import safe_relative
from cpa_manager.core.paths import ROOT
from cpa_manager.core.locking import update_lock
from cpa_manager.core import download as transfer
from cpa_manager.core.runtime import stop_directory_processes
from cpa_manager.core.backups import portable_backup_prefix

METADATA = ".github-install.json"
PACKAGE_CACHE = ROOT / ".download-cache" / "portable-packages"


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


def _file_digest(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(256 * 1024):
            if cancel is not None:
                transfer.check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _expected_asset_digest(asset):
    match = re.fullmatch(r"(?:sha256:)?([a-fA-F0-9]{64})", str(asset.get("digest") or ""))
    return match[1].lower() if match else None


def _package_cache(release, asset):
    record = {"repository": github.repository(release["repository"]).lower(), "version": release["tag"],
              "asset": asset["name"], "url": asset["url"]}
    key = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    return Path(PACKAGE_CACHE) / key, record


def _cached_package(cache, record, asset, destination, cancel=None):
    if not cache.is_dir():
        return False
    try:
        with update_lock(cache):
            saved = json.loads((cache / "metadata.json").read_text(encoding="utf-8"))
            package = cache / "package"
            if not isinstance(saved, dict) or any(saved.get(key) != value for key, value in record.items()):
                return False
            saved_digest = str(saved.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", saved_digest):
                return False
            expected = _expected_asset_digest(asset)
            if expected and saved_digest != expected:
                return False
            if saved.get("id") is not None and asset.get("id") is not None:
                if saved.get("id") != asset.get("id") or saved.get("updated_at") != asset.get("updated_at"):
                    return False
            if _file_digest(package, cancel).lower() != saved_digest:
                return False
            with package.open("rb") as source, Path(destination).open("wb") as output:
                while chunk := source.read(256 * 1024):
                    if cancel is not None:
                        transfer.check_cancel(cancel)
                    output.write(chunk)
            return True
    except (OSError, ValueError, RuntimeError):
        return False


def _discard_cached_package(cache):
    if not cache.is_dir():
        return
    try:
        with update_lock(cache):
            (cache / "package").unlink(missing_ok=True)
            (cache / "metadata.json").unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass


def _save_package(cache, record, asset, archive):
    cache.mkdir(parents=True, exist_ok=True)
    with update_lock(cache):
        package, metadata = cache / "package", cache / "metadata.json"
        pending = cache / ("package." + uuid.uuid4().hex + ".tmp")
        pending_metadata = cache / ("metadata." + uuid.uuid4().hex + ".tmp")
        try:
            shutil.copyfile(archive, pending)
            payload = dict(record, id=asset.get("id"), updated_at=asset.get("updated_at"),
                           sha256=_file_digest(pending))
            pending_metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(pending, package)
            os.replace(pending_metadata, metadata)
        finally:
            pending.unlink(missing_ok=True)
            pending_metadata.unlink(missing_ok=True)


def _download_fresh(release, asset, archive, proxy, report, cancel, workers, cache, record):
    if workers is not None and _accepts_workers(github.download):
        github.download(release, asset, archive, proxy, report, cancel, workers=workers)
    else:
        github.download(release, asset, archive, proxy, report, cancel)
    transfer.check_cancel(cancel)
    try:
        _save_package(cache, record, asset, archive)
        report(80, "下载完成，已缓存安装包，正在准备安装。")
    except (OSError, RuntimeError) as error:
        report(80, "下载完成，但缓存安装包失败，正在继续安装：" + str(error))


def _download_or_reuse(release, asset, archive, proxy, report, cancel, workers):
    if asset not in release["assets"]:
        raise ValueError("附件不属于当前 Release。")
    cache, record = _package_cache(release, asset)
    if _cached_package(cache, record, asset, archive, cancel):
        report(80, "已找到本地缓存安装包，跳过远程下载。")
        return True, cache, record
    _download_fresh(release, asset, archive, proxy, report, cancel, workers, cache, record)
    return False, cache, record


def _replace_target(pending_path, target):
    for attempt in range(4):
        try:
            os.replace(pending_path, target)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05)


def _record_repository(value):
    if isinstance(value, str) and value and "://" not in value:
        value = "https://github.com/" + value.lstrip("/")
    return github.repository(value)


def _software_records(data):
    """Normalize each record independently so fields never cross repositories."""
    records = {}
    items = data.get("software", {})
    if isinstance(items, dict):
        for key, item in items.items():
            if not isinstance(item, dict):
                continue
            try:
                repo = _record_repository(item.get("repository") or item.get("url") or item.get("repo") or key)
            except ValueError:
                continue
            records[repo.lower()] = dict(item, repository=repo,
                version=item.get("version") or item.get("tag") or "",
                asset=item.get("asset") or item.get("selected_asset") or item.get("package") or "")
    try:
        repo = _record_repository(data.get("repository") or data.get("url") or data.get("repo"))
    except ValueError:
        return records
    records.setdefault(repo.lower(), dict(data, repository=repo,
        version=data.get("version") or data.get("tag") or "",
        asset=data.get("asset") or data.get("selected_asset") or data.get("package") or ""))
    records[repo.lower()].pop("software", None)
    return records


def _owned_files(record):
    files = record.get("files", [])
    # Older single-EXE records still identify their executable unambiguously.
    if not isinstance(files, list):
        files = []
    asset = record.get("asset")
    if "files" not in record and isinstance(asset, str) and asset.lower().endswith(".exe"):
        files = [*files, asset]
    result = set()
    for value in files:
        try:
            result.add(safe_relative(value).as_posix().lower())
        except (ValueError, AttributeError, TypeError):
            continue
    return result


def _installation_files(root, content, files, protected, records, repository):
    owners = {}
    for key, record in records.items():
        if key != repository:
            for path in _owned_files(record):
                owners[path] = record["repository"]
    owner_paths = sorted(owners)
    selected = []
    for source in files:
        relative = source.relative_to(content)
        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise RuntimeError("安装路径通过链接指向软件目录外。")
        cursor = target
        while cursor != root:
            if cursor.is_symlink() or cursor.is_junction():
                raise RuntimeError("安装目标包含链接路径：" + str(relative))
            cursor = cursor.parent
        key = relative.as_posix().lower()
        if target.exists() and any(key == p or key.startswith(p + "/") for p in protected):
            continue
        prefixes = ["/".join(relative.parts[:index]).lower() for index in range(1, len(relative.parts) + 1)]
        conflict = next((path for path in prefixes if path in owners), None)
        position = bisect_left(owner_paths, key + "/")
        if conflict is None and position < len(owner_paths) and owner_paths[position].startswith(key + "/"):
            conflict = owner_paths[position]
        if conflict is not None:
            raise RuntimeError(f"安装文件 {relative} 与同目录软件 {owners[conflict]} 冲突，请选择独立安装目录。")
        if target.exists() and not target.is_file():
            raise RuntimeError("安装文件与已有目录冲突：" + str(relative))
        selected.append(source)
    return selected


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
        reused, cache, record = _download_or_reuse(release, asset, archive, proxy, report, cancel, workers)
        transfer.check_cancel(cancel)
        if asset["name"].lower().endswith(".zip"):
            try:
                content = extract(archive, stage)
            except zipfile.BadZipFile:
                if not reused:
                    raise
                _discard_cached_package(cache)
                shutil.rmtree(stage, ignore_errors=True)
                stage.mkdir(parents=True, exist_ok=True)
                _download_fresh(release, asset, archive, proxy, report, cancel, workers, cache, record)
                transfer.check_cancel(cancel)
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
        records = _software_records(previous)
        repository = github.repository(release["repository"]).lower()
        files = _installation_files(root, content, files, protected, records, repository)
        owned = _owned_files(records.get(repository, {}))
        owned.update(p.relative_to(content).as_posix().lower() for p in files)
        installed = {"repository": release["repository"], "version": release["tag"], "asset": asset["name"]}
        records[repository] = dict(installed, files=sorted(owned))
        (content / METADATA).write_text(json.dumps(dict(installed, software=records), ensure_ascii=False), encoding="utf-8")
        files.append(content / METADATA)
        transfer.check_cancel(cancel)
        stopped = stop_directory_processes(root, relative_files=[p.relative_to(content) for p in files])
        if stopped:
            report(82, "已关闭本次更新文件对应的运行程序：" + ", ".join(map(str, stopped)))
        transfer.check_cancel(cancel)
        backup = root / (portable_backup_prefix(release["repository"]) + uuid.uuid4().hex)
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
                    _replace_target(pending_path, target)
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
            raise RuntimeError("安装失败，已恢复修改的文件。原因：" + str(error)) from error


def read_installed_metadata(directory, excluded_repositories=()):
    if not directory:
        return None
    try:
        data = json.loads((Path(directory) / METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    excluded = set()
    for repo in excluded_repositories:
        try:
            excluded.add(_record_repository(repo).lower())
        except ValueError:
            pass
    records = _software_records(data)
    for key, item in records.items():
        if key not in excluded:
            return {field: str(item.get(field) or "").strip()
                    for field in ("repository", "version", "asset")}
    return None


def local_version(directory, repo=""):
    if not directory:
        return None
    try:
        data = json.loads((Path(directory) / METADATA).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        records = _software_records(data)
        if repo:
            return records.get(_record_repository(repo).lower(), {}).get("version") or None
        current = data.get("repository") or data.get("url") or data.get("repo")
        if current:
            return records.get(_record_repository(current).lower(), {}).get("version") or None
        if len(records) == 1:
            return next(iter(records.values())).get("version") or None
        return (data.get("version") or data.get("tag")) if not records else None
    except (OSError, ValueError, AttributeError):
        return None
