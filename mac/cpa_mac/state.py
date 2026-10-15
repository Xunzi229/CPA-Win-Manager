"""Saved manager settings."""
from pathlib import Path
import time
import uuid

from cpa_mac.backends.github import repository
from cpa_mac.backends.software import parse_preserve
from cpa_mac.config import DEFAULT_DOWNLOAD_WORKERS, normalize_download_workers
from cpa_mac.core.settings import load_settings, save_settings, support_dir


def _clean_project(saved, default):
    if not isinstance(saved, dict):
        return {"directory": default, "latest": "", "asset": "", "last_check": 0}
    directory = saved.get("directory") if isinstance(saved.get("directory"), str) and saved.get("directory").strip() else default
    latest = saved.get("latest") if isinstance(saved.get("latest"), str) else ""
    asset = saved.get("asset") if isinstance(saved.get("asset"), str) else ""
    last_check = saved.get("last_check") if isinstance(saved.get("last_check"), (int, float)) else 0
    return {"directory": directory, "latest": latest, "asset": asset, "last_check": last_check}


def _clean_software(items, portable):
    result, seen = [], set()
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name, repo = item.get("name"), item.get("repository")
        directory = item.get("directory") if isinstance(item.get("directory"), str) else ""
        if not isinstance(name, str) or not name.strip() or not isinstance(repo, str):
            continue
        try:
            address = repository(repo)
            parse_preserve(item.get("preserve") if isinstance(item.get("preserve"), str) else "")
        except ValueError:
            continue
        name = name.strip()
        if not name or name == "releases":
            name = address.rstrip("/").rsplit("/", 1)[-1]
        if portable and not directory.strip():
            continue
        identity = address.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append({
            "id": item["id"] if isinstance(item.get("id"), str) and item["id"] else uuid.uuid4().hex,
            "name": name.strip(),
            "repository": address,
            "directory": directory.strip(),
            "preserve": item.get("preserve").strip() if isinstance(item.get("preserve"), str) else "",
            "latest": item.get("latest") if isinstance(item.get("latest"), str) else "",
            "asset": item.get("asset") if isinstance(item.get("asset"), str) else "",
            "last_check": item.get("last_check") if isinstance(item.get("last_check"), (int, float)) else 0,
            "installed_id": item.get("installed_id", ""),
            "installed_name": item.get("installed_name", ""),
            "installed_version": item.get("installed_version", ""),
            "installed_dir": item.get("installed_dir", ""),
            "pinned": bool(item.get("pinned", False)),
        })
    return result


def load_data():
    saved = load_settings()
    support = support_dir()
    download = saved.get("download_directory") if isinstance(saved.get("download_directory"), str) else ""
    return {
        "proxy_enabled": saved.get("proxy_enabled") if type(saved.get("proxy_enabled")) is bool else False,
        "proxy": saved.get("proxy") if isinstance(saved.get("proxy"), str) and saved.get("proxy").strip() else "http://127.0.0.1:7890",
        "download_directory": download.strip() or str(Path.home() / "Downloads"),
        "download_workers": normalize_download_workers(saved.get("download_workers", DEFAULT_DOWNLOAD_WORKERS)),
        "prerelease": bool(saved.get("prerelease", False)),
        "cli": _clean_project(saved.get("cli"), str(support / "CLIProxyAPI")),
        "plus": _clean_project(saved.get("plus"), str(support / "CPA-Manager-Plus")),
        "portable": _clean_software(saved.get("portable"), True),
        "packages": _clean_software(saved.get("packages"), False),
        "manager_last_check": saved.get("manager_last_check") if isinstance(saved.get("manager_last_check"), (int, float)) else 0,
        "manager_release": saved.get("manager_release") if isinstance(saved.get("manager_release"), dict) else None,
    }


def save_data(data):
    save_settings({
        "proxy_enabled": data["proxy_enabled"],
        "proxy": data["proxy"],
        "download_directory": data["download_directory"],
        "download_workers": normalize_download_workers(data.get("download_workers", DEFAULT_DOWNLOAD_WORKERS)),
        "prerelease": bool(data.get("prerelease", False)),
        "cli": data["cli"],
        "plus": data["plus"],
        "portable": data["portable"],
        "packages": data["packages"],
        "manager_last_check": data.get("manager_last_check", 0),
        "manager_release": data.get("manager_release"),
    })


def export_settings_json():
    import json
    return json.dumps(load_data(), ensure_ascii=False, indent=2)


def import_settings_json(json_text):
    import json
    data = json.loads(json_text)
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须为 JSON 对象。")
    save_data({
        "proxy_enabled": bool(data.get("proxy_enabled", False)),
        "proxy": str(data.get("proxy") or "http://127.0.0.1:7890"),
        "download_directory": str(data.get("download_directory") or (Path.home() / "Downloads")),
        "download_workers": normalize_download_workers(data.get("download_workers", DEFAULT_DOWNLOAD_WORKERS)),
        "prerelease": bool(data.get("prerelease", False)),
        "cli": _clean_project(data.get("cli"), str(support_dir() / "CLIProxyAPI")),
        "plus": _clean_project(data.get("plus"), str(support_dir() / "CPA-Manager-Plus")),
        "portable": _clean_software(data.get("portable"), True),
        "packages": _clean_software(data.get("packages"), False),
        "manager_last_check": data.get("manager_last_check", 0),
        "manager_release": data.get("manager_release"),
    })
