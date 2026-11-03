"""Exercise both platforms against an actual local mirror, without GitHub traffic."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from cpa_manager.core import github_mirror as routing
from cpa_manager.core import download
from cpa_manager.core.network import network


def mac_module(name, filename):
    path = Path(__file__).resolve().parents[1] / "mac/cpa_mac/core" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mac_routing = mac_module("mac_mirror", "github_mirror.py")


class GitHubMirrorTests(unittest.TestCase):
    def test_mirror_http_refusal_retries_official_once_and_keeps_http_proxy(self):
        for module in (routing, mac_routing):
            for code in (403, 429, 502):
                calls, messages = [], []

                @module.download_fallback
                def verified_operation(proxy, report):
                    calls.append(proxy)
                    if proxy.github_mirror:
                        raise urllib.error.HTTPError(proxy.github_mirror + "https://github.com/o/r/releases/download/v1/a.zip", code, "Denied", {}, None)
                    return "verified"

                route = module.NetworkRoute("http://127.0.0.1:7890", "https://mirror.example/")
                self.assertEqual(verified_operation(route, lambda _, text: messages.append(text)), "verified")
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[1], route)
                self.assertEqual(calls[1].github_mirror, "")
                self.assertEqual(route.github_mirror, "https://mirror.example/")
                self.assertTrue(any(f"HTTP {code}" in message and "回退" in message for message in messages))

    def test_metadata_errors_and_integrity_failures_never_change_source(self):
        for module in (routing, mac_routing):
            for failure in (urllib.error.HTTPError("https://api.github.com/repos/o/r/releases", 403, "Denied", {}, None),
                            RuntimeError("SHA256 mismatch")):
                calls = []

                @module.download_fallback
                def operation(proxy, report):
                    calls.append(proxy)
                    raise failure

                with self.assertRaises(type(failure)):
                    operation(module.NetworkRoute("", "https://mirror.example/"), lambda *_: None)
                self.assertEqual(len(calls), 1)

    def test_both_sources_failing_produces_actionable_error_without_a_loop(self):
        for module in (routing, mac_routing):
            calls = []

            @module.download_fallback
            def operation(proxy, report):
                calls.append(proxy)
                url = (proxy.github_mirror or "https://github.com/") + "file"
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

            with self.assertRaisesRegex(RuntimeError, "官方源仍失败"):
                operation(module.NetworkRoute("", "https://mirror.example/"), lambda *_: None)
            self.assertEqual(len(calls), 2)

    def test_mirror_defaults_to_direct_through_redirects_and_logs_hide_secrets(self):
        requests, messages = [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                requests.append(self.path)
                if self.path.startswith("/mirror/"):
                    self.send_response(302)
                    self.send_header("Location", "/payload?token=download-secret")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "7")
                    self.end_headers()
                    self.wfile.write(b"payload")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            mac_root = str(Path(__file__).resolve().parents[1] / "mac")
            sys.path.insert(0, mac_root)
            try:
                from cpa_mac.core.network import network as mac_network
            finally:
                sys.path.remove(mac_root)
            for module, connect in ((routing, network), (mac_routing, mac_network)):
                route = module.logged_route(module.NetworkRoute("http://user:proxy-secret@127.0.0.1:1",
                                            f"http://127.0.0.1:{server.server_port}/mirror/"),
                                            lambda _, text: messages.append(text))
                with patch("urllib.request.proxy_bypass", return_value=False):
                    with connect(route).open("https://github.com/o/r/releases/download/v1/a.zip?token=download-secret", timeout=3) as response:
                        self.assertEqual(response.read(), b"payload")
            text = "\n".join(messages)
            self.assertNotIn("proxy-secret", text)
            self.assertNotIn("download-secret", text)
            self.assertNotIn("user:", text)
            self.assertIn("HTTP 302", text)
            self.assertIn("HTTP 200", text)
            self.assertIn("HTTP 代理=直连", text)
            self.assertEqual(len(requests), 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_saved_mirror_survives_loading_with_http_proxy_disabled(self):
        from cpa_manager.config import shared_proxy_settings
        saved = {"proxy_settings": {"enabled": False, "url": "", "github_mirror": "https://ghfast.top"}}
        settings = shared_proxy_settings(saved, {})
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["github_mirror"], "https://ghfast.top/")

    def test_only_download_urls_are_rewritten_without_losing_encoding(self):
        for module in (routing, mac_routing):
            with self.subTest(platform=module.__name__):
                prefix = "https://mirror.example/prefix/"
                for url in ("https://github.com/owner/tool/releases/download/v1/a%20b.zip?token=abc",
                            "https://github.com/owner/tool/releases/latest/download/a.zip",
                            "https://github.com/owner/tool/archive/refs/tags/v1.zip",
                            "https://raw.githubusercontent.com/owner/tool/main/checksums.txt"):
                    self.assertEqual(module.mirror_url(url, prefix), prefix + url)
                    self.assertEqual(module.mirror_url(url, ""), url)
                    self.assertEqual(module.mirror_url(prefix + url, prefix), prefix + url)
                for url in ("https://api.github.com/repos/owner/tool/releases", "https://github.com/owner/tool/releases",
                            "https://github.com/owner/tool/releases/expanded_assets/v1", "https://github.com/search?q=tool",
                            "https://github.com.evil.example/a/releases/download/v1/a.zip", "https://example.com/a.zip"):
                    self.assertEqual(module.mirror_url(url, prefix), url)

    def test_validation_normalizes_paths_and_rejects_invalid_prefixes(self):
        for module in (routing, mac_routing):
            with self.subTest(platform=module.__name__):
                self.assertEqual(module.normalize_mirror(" https://host.example/path/// "), "https://host.example/path/")
                self.assertEqual(module.normalize_mirror("  "), "")
                for invalid in (None, "gh-proxy.com", "ftp://host", "https://", "https://host:bad", "https://host:0",
                                "https://user:pass@host", "https://@host", "https://host?q=x", "https://host#",
                                "https://host?", "https://ho st", "https://host\\evil"):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        module.normalize_mirror(invalid)

    def test_http_proxy_and_mirror_are_carried_independently(self):
        for module in (routing, mac_routing):
            route = module.NetworkRoute("http://127.0.0.1:7890", "https://ghfast.top")
            self.assertEqual(route, "http://127.0.0.1:7890")
            self.assertEqual(route.github_mirror, "https://ghfast.top/")
            request = urllib.request.Request("https://github.com/o/r/releases/download/v1/a.zip",
                                             headers={"Range": "bytes=100-199", "If-Range": '"etag"'})
            module.GitHubMirrorHandler(route.github_mirror).https_request(request)
            self.assertEqual(request.get_header("Range"), "bytes=100-199")
            self.assertEqual(request.get_header("If-range"), '"etag"')

    def test_real_mirror_serves_range_download_and_checksums(self):
        from unittest.mock import patch
        from cpa_manager.backends import github
        data = bytes(range(256)) * 8192
        digest = hashlib.sha256(data).hexdigest()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Range"), self.headers.get("Host")))
                content = (digest + "  app.zip\n").encode() if self.path.endswith("checksums.txt") else data
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                start, end = (int(match[1]), int(match[2])) if match else (0, len(content) - 1)
                self.send_response(206 if match else 200)
                if match:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(content)}")
                self.send_header("ETag", '"release-v1"')
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(content[start:end + 1])

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        prefix = f"http://127.0.0.1:{server.server_port}/mirror/"
        original = "https://github.com/owner/tool/releases/download/v1/"
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                route = routing.NetworkRoute("", prefix)
                asset = {"name": "app.zip", "url": original + "app.zip"}
                release = {"assets": [asset, {"name": "checksums.txt", "url": original + "checksums.txt"}]}
                with patch.object(download, "ROOT", root):
                    github.download(release, asset, root / "win.zip", route, lambda *_: None, workers=2)
                self.assertEqual((root / "win.zip").read_bytes(), data)
                self.assertTrue(any(path.endswith("checksums.txt") for path, _, _ in requests))
                self.assertEqual(len([item for item in requests if item[1] and item[1] != "bytes=0-0"]), 2)
                self.assertTrue(all(path.startswith("/mirror/" + original) for path, _, _ in requests))
                self.assertTrue(all(host == f"127.0.0.1:{server.server_port}" for _, _, host in requests))
                combined = routing.NetworkRoute(f"http://127.0.0.1:{server.server_port}", "http://mirror.invalid/", mirror_use_proxy=True)
                with network(combined).open(original + "app.zip", timeout=5) as response:
                    self.assertEqual(response.read(), data)
                self.assertEqual(requests[-1][0], "http://mirror.invalid/" + original + "app.zip")
                requests.clear()
                mac_root = str(Path(__file__).resolve().parents[1] / "mac")
                sys.path.insert(0, mac_root)
                try:
                    from cpa_mac.backends import github as mac_github
                    mac_github.download_verified(release, asset, root / "mac.zip", route, lambda *_: None, workers=2)
                finally:
                    sys.path.remove(mac_root)
                self.assertEqual((root / "mac.zip").read_bytes(), data)
                self.assertTrue(any(path.endswith("checksums.txt") for path, _, _ in requests))
                self.assertEqual(len([item for item in requests if item[1] and item[1] != "bytes=0-0"]), 2)
                self.assertTrue(all(path.startswith("/mirror/" + original) for path, _, _ in requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
