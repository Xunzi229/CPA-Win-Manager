"""Release cache stored separately from small user settings."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from cpa_manager.backends import github


class ReleaseCache:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None
        self.items = {}
        self.dirty = set()

    def key(self, repo):
        return github.repository(repo).lower()

    def path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, repo):
        try:
            key = self.key(repo)
        except ValueError:
            return []
        if key not in self.items:
            try:
                data = json.loads(self.path(key).read_text(encoding="utf-8")) if self.directory else []
            except (OSError, ValueError):
                data = []
            self.items[key] = data if github.valid_catalog(data, repo) else []
        return self.items[key]

    def put(self, repo, catalog):
        if not github.valid_catalog(catalog, repo):
            return False
        key = self.key(repo)
        if self.get(repo) != catalog:
            self.items[key] = catalog
            self.dirty.add(key)
        return True

    def flush(self):
        if not self.directory:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        for key in list(self.dirty):
            target = self.path(key)
            temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(self.items[key], ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, target)
                self.dirty.remove(key)
            finally:
                temporary.unlink(missing_ok=True)
