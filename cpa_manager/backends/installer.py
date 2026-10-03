"""Installer catalog, release selection, reusable downloads and owned-file cleanup."""
import copy
import hashlib
from pathlib import Path
import uuid

from cpa_manager.backends import github
from cpa_manager.core.files import safe_relative
from cpa_manager.core.models import ensure_ids
import shutil
import tempfile


def repo_key(value):
    return github.repository(value).lower()


def load_profiles(saved, legacy=()):
    result, seen = [], set()
    for source in list(saved if isinstance(saved, list) else []) + list(legacy):
        if not isinstance(source, dict):
            continue
        try:
            repo = github.repository(source.get("repository", ""))
        except (ValueError, AttributeError):
            continue
        key = repo_key(repo)
        if key in seen:
            continue
        seen.add(key)
        result.append({"id": source.get("id"), "name": str(source.get("name") or repo.rsplit("/", 1)[1]),
                       "pinned": source.get("pinned") is True,
                       "installed_id": source.get("installed_id") if isinstance(source.get("installed_id"), str) else "",
                       "installed_auto": source.get("installed_auto") is not False,
                       "repository": repo, "selected_asset": str(source.get("selected_asset") or ""),
                       "release": source.get("release") if github.valid_release(source.get("release"), repo) else None,
                       "history": [r for r in source.get("history", []) if isinstance(r, dict)]
                                  if isinstance(source.get("history"), list) else []})
    ensure_ids(result)
    return result


def refresh(profile, proxy):
    profile = copy.deepcopy(profile)
    release = github.releases(profile["repository"], proxy)
    assets = github.candidates(release, "安装器")
    selected = next((a for a in assets if a["name"] == profile.get("selected_asset")), None)
    if selected is None:
        selected = github.recommended_asset(assets, "安装器")
    profile.update(release=release, selected_asset=selected["name"] if selected else "")
    return profile


def selected_asset(profile):
    release = profile.get("release")
    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        return None
    return next((a for a in github.candidates(release, "安装器")
                 if a["name"] == profile.get("selected_asset")), None)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(256 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cached_download(profile, verify=False):
    asset = selected_asset(profile)
    if asset is None:
        return None
    for record in reversed(profile.get("history", [])):
        if (record.get("version") == profile["release"].get("tag")
                and record.get("asset") == asset["name"]
                and record.get("identity") == github.asset_identity(asset)
                and record.get("url") == asset["url"]):
            try:
                path = Path(record["path"])
                if path.is_file() and (not verify or file_digest(path) == record.get("sha256")):
                    return path
            except (OSError, KeyError, TypeError):
                pass
    return None


def downloaded_version(profile):
    for record in reversed(profile.get("history", [])):
        try:
            if repo_key(record.get("repository", profile["repository"])) != repo_key(profile["repository"]):
                continue
            if Path(record["path"]).is_file():
                return str(record.get("version", "—"))
        except (KeyError, OSError, TypeError, ValueError):
            pass
    return "—"


def state(profile):
    if not profile.get("release"):
        return "尚未检查"
    if selected_asset(profile) is None:
        return "请选择附件"
    if cached_download(profile):
        return "最新已下载"
    return "待更新" if downloaded_version(profile) != "—" else "待下载"


def folder_name(profile):
    return "installers-" + hashlib.sha256(repo_key(profile["repository"]).encode()).hexdigest()[:16]


def prepare(profile, directory, proxy, report, cancel=None):
    """Return a downloaded installer; never execute it or claim it was installed."""
    profile = copy.deepcopy(profile)
    github.transfer.check_cancel(cancel)
    asset = selected_asset(profile)
    if asset is None:
        raise ValueError("请先检查最新版本并选择对应的 EXE / MSI 附件。")
    cached = cached_download(profile, verify=True)
    if cached:
        github.transfer.check_cancel(cancel)
        report(100, "此版本和附件已下载，直接复用本地安装包。")
        return profile, cached
    folder = Path(directory).expanduser().resolve() / folder_name(profile)
    path = download_installer(profile["release"], asset, folder, proxy, report, cancel)
    sha256 = file_digest(path)
    github.transfer.check_cancel(cancel)
    profile.setdefault("history", []).append({"version": profile["release"]["tag"],
        "asset": asset["name"], "url": asset["url"], "identity": github.asset_identity(asset),
        "path": str(path), "directory": str(folder), "sha256": sha256, "repository": profile["repository"]})
    report(100, "安装包已保存：" + str(path))
    return profile, path


def clear_history(profile):
    """Remove only recorded installer files in this repository's managed folders."""
    profile = copy.deepcopy(profile)
    remaining, errors = [], []
    original_history = list(profile.get("history", []))
    for record in original_history:
        try:
            folder = Path(record["directory"])
            path = Path(record["path"])
            owner = dict(profile, repository=record.get("repository", profile["repository"]))
            if (folder.name != folder_name(owner) or path.parent.resolve() != folder.resolve()
                    or not path.name.startswith("installer-") or path.suffix.lower() not in (".exe", ".msi")
                    or path.is_symlink() or folder.is_symlink()
                    or (hasattr(folder, "is_junction") and folder.is_junction())):
                raise ValueError("下载记录的路径不属于该软件的安装包目录。")
            path.unlink(missing_ok=True)
        except (OSError, KeyError, ValueError, TypeError) as error:
            remaining.append(record)
            errors.append(str(error))
    profile["history"] = remaining
    # Also clear resumable parts for known release assets, so cleanup reclaims download space.
    release = profile.get("release") or {}
    cached_assets = list(release.get("assets", []))
    cached_assets.extend({"url": r.get("url"), "identity": r.get("identity", "")} for r in original_history)
    for asset in cached_assets:
        if isinstance(asset, dict) and asset.get("url"):
            try:
                github.transfer.discard(asset["url"], asset.get("identity", github.asset_identity(asset)))
            except (OSError, RuntimeError) as error:
                errors.append(str(error))
    return profile, errors


def download_installer(release, asset, directory, proxy, report, cancel=None):
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = safe_relative(asset["name"])
    if len(filename.parts) != 1 or filename.suffix.lower() not in (".exe", ".msi"):
        raise ValueError("安装器必须是 EXE 或 MSI 附件。")
    with tempfile.TemporaryDirectory(prefix="github-installer-") as temporary:
        source = Path(temporary) / "installer"
        github.download(release, asset, source, proxy, report, cancel)
        github.transfer.check_cancel(cancel)
        report(80, "下载完成，正在保存安装器。")
        # Never overwrite an existing executable in the download folder.
        output = root / ("installer-" + uuid.uuid4().hex[:8] + "-" + filename.name)
        if isinstance(cancel, github.transfer.DownloadControl):
            cancel.begin_commit()
        shutil.copy2(source, output)
    return output
