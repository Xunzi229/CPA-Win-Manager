"""Release cache stored separately from small user settings."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from cpa_manager.backends import github


class ReleaseCache:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None
        self.items = {}
        self.dirty = set()

    def key(self, repo, include_prerelease=False):
        suffix = ":prerelease" if include_prerelease else ""
        return (github.repository(repo) + suffix).lower()

    def path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, repo, include_prerelease=False, max_age=None):
        try:
            key = self.key(repo, include_prerelease=include_prerelease)
        except ValueError:
            return []
        if key not in self.items:
            try:
                raw = json.loads(self.path(key).read_text(encoding="utf-8")) if self.directory else []
            except (OSError, ValueError):
                raw = []
            if isinstance(raw, dict) and "catalog" in raw:
                data = raw.get("catalog", [])
                ts = raw.get("timestamp", 0)
            else:
                data = raw if isinstance(raw, list) else []
                ts = 0
            if github.valid_catalog(data, repo):
                self.items[key] = (ts, data)
            else:
                self.items[key] = (0, [])
        ts, data = self.items[key]
        if max_age is not None and (time.time() - ts > max_age):
            return []
        return data

    def put(self, repo, catalog, include_prerelease=False, timestamp=None):
        if not github.valid_catalog(catalog, repo):
            return False
        key = self.key(repo, include_prerelease=include_prerelease)
        ts = timestamp if timestamp is not None else time.time()
        old_data = self.get(repo, include_prerelease=include_prerelease)
        if old_data != catalog:
            self.dirty.add(key)
        self.items[key] = (ts, catalog)
        return True

    def clear(self):
        self.items.clear()
        self.dirty.clear()
        if self.directory and self.directory.exists():
            for child in self.directory.glob("*.json"):
                try:
                    child.unlink(missing_ok=True)
                except OSError:
                    pass

    def flush(self):
        if not self.directory:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        for key in list(self.dirty):
            target = self.path(key)
            temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                entry = self.items.get(key)
                if not entry:
                    continue
                ts, catalog = entry
                temporary.write_text(json.dumps({"timestamp": ts, "catalog": catalog}, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, target)
                self.dirty.remove(key)
            finally:
                temporary.unlink(missing_ok=True)
