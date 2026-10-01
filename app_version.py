"""Release version embedded in the packaged manager."""
import json
from pathlib import Path
import sys

SOURCE_VERSION = "1.1.0"


def current_version():
    if getattr(sys, "frozen", False):
        metadata = Path(sys._MEIPASS) / "app-version.json"
        return json.loads(metadata.read_text(encoding="utf-8-sig"))["version"]
    return SOURCE_VERSION
