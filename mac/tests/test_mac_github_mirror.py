"""Mirror settings survive storage and reach both Mac business entry points."""
import json
from pathlib import Path
import sys
import unittest
import urllib.error
import tempfile
import hashlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpa_mac import state, native_worker, webapp
from cpa_mac.backends import github


class MacGitHubMirrorTests(unittest.TestCase):
    def test_verified_download_preserves_mirror_http_error_for_official_fallback(self):
        from cpa_mac.core.github_mirror import NetworkRoute
        data = b"verified fixture"
        digest = hashlib.sha256(data).hexdigest()
        calls = []

        def stream(route, url, destination, report, cancel):
            calls.append(route)
            if route.github_mirror:
                raise urllib.error.HTTPError(route.github_mirror + url, 403, "Forbidden", {}, None)
            destination.write_bytes(data)
            return digest

        asset = {"name": "app.zip", "url": "https://github.com/o/r/releases/download/v1/app.zip", "digest": "sha256:" + digest}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(github, "network", side_effect=lambda route, **_: route), \
             patch.object(github, "_download_stream", side_effect=stream):
            actual = github.download_verified({"assets": [asset]}, asset, Path(folder) / "app.zip",
                         NetworkRoute("http://127.0.0.1:7890", "https://mirror.example/"), lambda *_: None, workers=1)
        self.assertEqual(actual, digest)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].github_mirror, "")
        self.assertEqual(calls[1], "http://127.0.0.1:7890")

    def test_ignored_range_falls_back_instead_of_corrupting_the_package(self):
        payload = bytes(index % 251 for index in range(2 * 1024 * 1024))

        class Response:
            def __init__(self, status, headers, body):
                self.status, self.headers, self._body, self._pos = status, headers, body, 0

            def read(self, size):
                chunk = self._body[self._pos:self._pos + size]
                self._pos += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Opener:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout=None):
                header = None if isinstance(request, str) else request.get_header("Range")
                self.calls.append(header)
                if header == "bytes=0-0":
                    return Response(206, {"Content-Range": f"bytes 0-0/{len(payload)}"}, payload[:1])
                if header:
                    # Probe succeeded, but the real range request returns the whole file.
                    return Response(200, {"Content-Length": str(len(payload))}, payload)
                return Response(200, {"Content-Length": str(len(payload))}, payload)

        opener = Opener()
        with tempfile.TemporaryDirectory() as folder, patch.object(github, "network", return_value=opener):
            destination = Path(folder) / "app.dmg"
            github.download("https://github.com/o/r/releases/download/v1/app.dmg", destination,
                            lambda *_: None, workers=4)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertIn(None, opener.calls)

    def test_chunked_download_keeps_exact_ranges(self):
        payload = bytes(range(64)) * 100

        class Response:
            def __init__(self, body, status, headers):
                self.status, self.headers, self._body, self._pos = status, headers, body, 0

            def read(self, size):
                chunk = self._body[self._pos:self._pos + size]
                self._pos += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Opener:
            def open(self, request, timeout=None):
                header = request.get_header("Range")
                start, end = (int(part) for part in header.removeprefix("bytes=").split("-"))
                body = payload[start:end + 1]
                return Response(body, 206, {"Content-Range": f"bytes {start}-{end}/{len(payload)}"})

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "app.dmg"
            digest = github._download_chunked(Opener(), "https://example.test/app.dmg", destination,
                                              len(payload), 4, lambda *_: None, None)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_old_settings_remain_direct_and_mirror_round_trips(self):
        with patch.object(state, "load_settings", return_value={}):
            data = state.load_data()
        self.assertEqual(data["github_mirror"], "")
        self.assertFalse(data["mirror_use_proxy"])
        data["github_mirror"] = "https://ghfast.top"
        data["mirror_use_proxy"] = True
        with patch.object(state, "save_settings") as save:
            state.save_data(data)
        saved = save.call_args.args[0]
        self.assertEqual(saved["github_mirror"], "https://ghfast.top/")
        self.assertTrue(saved["mirror_use_proxy"])
        with patch.object(state, "load_settings", return_value=saved):
            self.assertEqual(state.load_data()["github_mirror"], "https://ghfast.top/")
        with patch.object(state, "save_settings") as save:
            state.import_settings_json(json.dumps(saved))
        self.assertEqual(save.call_args.args[0]["github_mirror"], "https://ghfast.top/")
        self.assertTrue(save.call_args.args[0]["mirror_use_proxy"])

    def test_web_settings_validate_before_mutating_and_can_restore_official(self):
        with patch.object(webapp, "load_data", return_value={
                "proxy_enabled": False, "proxy": "", "github_mirror": "", "download_directory": "/tmp"}):
            store = webapp.Store()
        handler = object.__new__(webapp.Handler)
        body = {"proxy_enabled": False, "proxy": "", "github_mirror": "https://gh-proxy.com", "download_directory": "/tmp"}
        with patch.object(store, "save"):
            handler._post("/api/settings", body, store)
            self.assertEqual(store.proxy().github_mirror, "https://gh-proxy.com/")
            body["github_mirror"] = "invalid"
            with self.assertRaises(ValueError):
                handler._post("/api/settings", body, store)
            self.assertEqual(store.proxy().github_mirror, "https://gh-proxy.com/")
            body["github_mirror"] = ""
            handler._post("/api/settings", body, store)
            self.assertEqual(store.proxy().github_mirror, "")

    def test_native_worker_captures_mirror_with_http_proxy_disabled(self):
        request = {"config": {"proxy_enabled": False, "proxy": "", "github_mirror": "https://ghfast.top/",
                              "prerelease": False, "download_workers": 4},
                   "scope": "system", "action": "manager_update", "source_root": "."}
        with patch.object(github, "fetch_latest", return_value={"tag": "v1.5.20"}) as latest:
            native_worker.execute(request, lambda _: None)
        route = latest.call_args.args[1]
        self.assertEqual(route, "")
        self.assertEqual(route.github_mirror, "https://ghfast.top/")
