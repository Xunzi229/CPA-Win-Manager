"""Saved manager settings."""
from pathlib import Path
import uuid

from cpa_mac.backends.github import repository
from cpa_mac.backends.software import parse_preserve
from cpa_mac.core.settings import load_settings, save_settings, support_dir


def _clean_project(saved, default):
    if not isinstance(saved, dict):
        return {"directory": default, "latest": ""}
    directory = saved.get("directory") if isinstance(saved.get("directory"), str) and saved.get("directory").strip() else default
    latest = saved.get("latest") if isinstance(saved.get("latest"), str) else ""
    asset = saved.get("asset") if isinstance(saved.get("asset"), str) else ""
    return {"directory": directory, "latest": latest, "asset": asset}


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
        "cli": _clean_project(saved.get("cli"), str(support / "CLIProxyAPI")),
        "plus": _clean_project(saved.get("plus"), str(support / "CPA-Manager-Plus")),
        "portable": _clean_software(saved.get("portable"), True),
        "packages": _clean_software(saved.get("packages"), False),
    }


def save_data(data):
    save_settings({
        "proxy_enabled": data["proxy_enabled"],
        "proxy": data["proxy"],
        "download_directory": data["download_directory"],
        "cli": data["cli"],
        "plus": data["plus"],
        "portable": data["portable"],
        "packages": data["packages"],
    })
