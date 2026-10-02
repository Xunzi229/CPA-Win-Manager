import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
import threading
import unittest

from cpa_manager.core import download as download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.data = bytes(range(256)) * (20 * 1024)  # 5 MB, four distinct ranges
        self.etag = '"version-1"'
        self.ranges = []
        self.support_ranges = True
        self.bad_range = False
        self.drop_once = False
        state = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                start, end = (int(match[1]), int(match[2])) if match and state.support_ranges else (0, len(state.data) - 1)
                state.ranges.append((start, end))
                data = state.data[start:end + 1]
                self.send_response(206 if match and state.support_ranges else 200)
                if match and state.support_ranges:
                    self.send_header("Content-Range", f"bytes {start + int(state.bad_range)}-{end}/{len(state.data)}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("ETag", state.etag)
                self.end_headers()
                try:
                    if state.drop_once and end > 0:
                        state.drop_once = False
                        self.wfile.write(data[:65536])
                        self.wfile.flush()
                        self.close_connection = True
                    else:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/package"
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def fetch(self, report=lambda *_: None, cancel=None):
        return download.fetch(self.url, self.root / "result", report,
                              cache_root=self.cache, cancel=cancel)

    def test_parallel_download_merges_exact_bytes(self):
        messages = []
        digest = self.fetch(lambda _, text: messages.append(text))
        self.assertEqual(digest, hashlib.sha256(self.data).hexdigest())
        self.assertEqual((self.root / "result").read_bytes(), self.data)
        self.assertEqual(len([r for r in self.ranges if r[1] > 0]), 4)
        self.assertTrue(any("4 线程" in text for text in messages))

    def test_cancel_and_resume_in_a_new_download(self):
        cancel = threading.Event()
        def report(progress, text):
            if progress and progress > 10:
                cancel.set()
        with self.assertRaises(download.DownloadCancelled):
            self.fetch(report, cancel)
        parts = list(self.cache.glob("*/*.part"))
        self.assertTrue(any(p.stat().st_size > 0 for p in parts))
        initial = sum(p.stat().st_size for p in parts)
        self.assertLess(initial, len(self.data))
        messages = []
        self.ranges.clear()
        self.fetch(lambda _, text: messages.append(text))
        self.assertEqual((self.root / "result").read_bytes(), self.data)
        self.assertTrue(any("继续已下载" in text for text in messages))
        expected_starts = {len(self.data) * i // 4 for i in range(4)}
        self.assertTrue(any(start not in expected_starts for start, end in self.ranges if end > 0))

    def test_server_change_discards_cached_segments(self):
        self.fetch()
        self.data = b"Z" * len(self.data)
        self.etag = '"version-2"'
        self.ranges.clear()
        self.fetch()
        self.assertEqual((self.root / "result").read_bytes(), self.data)
        self.assertEqual(len([r for r in self.ranges if r[1] > 0]), 4)

    def test_no_range_support_falls_back_and_can_cancel(self):
        self.support_ranges = False
        messages = []
        self.fetch(lambda _, text: messages.append(text))
        self.assertEqual((self.root / "result").read_bytes(), self.data)
        self.assertTrue(any("单线程" in text for text in messages))
        cancel = threading.Event()
        def report(progress, _text):
            if progress and progress > 10:
                cancel.set()
        with self.assertRaises(download.DownloadCancelled):
            self.fetch(report, cancel)

    def test_wrong_content_range_is_not_merged(self):
        # Only corrupt worker responses, preserving a successful range probe.
        def report(_, text):
            if "使用" in text:
                self.bad_range = True
        with self.assertRaisesRegex(RuntimeError, "范围不一致"):
            self.fetch(report)
        self.assertFalse((self.root / "result").exists())

    def test_interrupted_connection_retries_missing_bytes(self):
        self.drop_once = True
        self.fetch()
        self.assertEqual((self.root / "result").read_bytes(), self.data)
        self.assertGreater(len([r for r in self.ranges if r[1] > 0]), 4)

    def test_live_download_pauses_then_resumes(self):
        control = download.DownloadControl()
        paused = threading.Event()
        results = []
        def report(progress, _text):
            if progress and progress > 10 and not paused.is_set():
                control.pause()
                paused.set()
        def worker():
            try:
                results.append(self.fetch(report, control))
            except Exception as error:
                results.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(paused.wait(5))
            self.assertTrue(thread.is_alive())
            self.assertFalse((self.root / "result").exists())
            control.resume()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results, [hashlib.sha256(self.data).hexdigest()])
            self.assertEqual((self.root / "result").read_bytes(), self.data)
        finally:
            control.set()
            control.resume()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
