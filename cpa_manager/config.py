"""Project defaults, legacy settings migration and version comparison."""
import json
from pathlib import Path
from cpa_manager.backends import cli as cli_backend
from cpa_manager.backends import plus as plus_backend
from cpa_manager.core.paths import ROOT

PROJECTS = {
    "cli": ("CLIProxyAPI", "cli-proxy-api.exe", cli_backend),
    "plus": ("CPA-Manager-Plus", "cpa-manager-plus.exe", plus_backend),
}


def has_update(local, latest):
    local_key = cli_backend.version_key(local) if local else None
    latest_key = cli_backend.version_key(latest) if latest else None
    return local_key is not None and latest_key is not None and latest_key > local_key


def shared_proxy_settings(saved, profiles):
    shared = saved.get("proxy_settings") if isinstance(saved, dict) else None
    if isinstance(shared, dict) and type(shared.get("enabled")) is bool and isinstance(shared.get("url"), str):
        return {"enabled": shared["enabled"], "url": shared["url"]}
    # Migrate an enabled legacy proxy first; CLI wins when both had different proxies.
    candidates = [profiles[key] for key in PROJECTS]
    chosen = next((item for item in candidates if item.get("proxy_enabled") and item.get("proxy")), candidates[0])
    return {"enabled": bool(chosen.get("proxy_enabled", False)), "url": chosen.get("proxy", "http://127.0.0.1:7890")}


def default_profiles():
    profiles = {}
    for key, (name, executable, _) in PROJECTS.items():
        directory = ROOT / name
        proxy, enabled = "http://127.0.0.1:7890", False
        # Import settings from either existing standalone manager without changing it.
        prefix = "CLIProxyAPI*" if key == "cli" else "cpa-manager-plus*"
        for candidate in sorted(ROOT.parent.glob(prefix)):
            if not (candidate / executable).is_file():
                continue
            directory = candidate
            settings = candidate / "update" / "update-settings.json"
            try:
                old = json.loads(settings.read_text(encoding="utf-8"))
                directory = Path(old.get("server_dir") or candidate)
                proxy = old.get("proxy", proxy)
                enabled = old.get("proxy_enabled", False)
            except (OSError, ValueError, TypeError):
                pass
            break
        profiles[key] = {"directory": str(directory), "proxy": proxy, "proxy_enabled": enabled, "checks": {}, "latest": {}}
    return profiles

from cpa_manager.core.download import (
    DEFAULT_DOWNLOAD_WORKERS,
    MIN_DOWNLOAD_WORKERS,
    MAX_DOWNLOAD_WORKERS,
)


def parse_download_workers(value, default=DEFAULT_DOWNLOAD_WORKERS):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    raw = str(value).strip()
    try:
        workers = int(raw)
    except (ValueError, TypeError):
        raise ValueError(f"下载分块数必须是整数。")
    if workers < MIN_DOWNLOAD_WORKERS:
        workers = MIN_DOWNLOAD_WORKERS
    elif workers > MAX_DOWNLOAD_WORKERS:
        workers = MAX_DOWNLOAD_WORKERS
    return workers

from cpa_manager.ui.widgets.table_resizer import (
    MIN_TABLE_ROWS,
    MAX_TABLE_ROWS,
    DEFAULT_PORTABLE_ROWS,
    DEFAULT_INSTALLER_ROWS,
    MIN_ROW_HEIGHT,
    MAX_ROW_HEIGHT,
    DEFAULT_ROW_HEIGHT,
    ROW_HEIGHT_PRESETS,
    clamp_rows,
    clamp_row_height,
)

parse_table_rows = clamp_rows
parse_table_row_height = clamp_row_height
DEFAULT_TABLE_ROW_HEIGHT = DEFAULT_ROW_HEIGHT
