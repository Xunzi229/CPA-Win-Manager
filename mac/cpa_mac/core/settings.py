"""Settings stored for the current macOS user."""
import json
import os
from pathlib import Path
import uuid


def support_dir():
    override = os.environ.get("CPA_MAC_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "CPA-Mac-Manager"


def settings_path():
    return support_dir() / "settings.json"


def load_settings():
    path = settings_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as error:
        raise OSError("配置文件损坏，原文件未修改。") from error
    if not isinstance(value, dict):
        raise OSError("配置根节点必须为对象。")
    return value


def save_settings(payload):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".settings-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
