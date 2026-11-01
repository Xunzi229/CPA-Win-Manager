"""Release discovery distinguishes network failures from missing attachments."""
import http.client
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from cpa_manager.backends import cli, plus
from cpa_manager.core.network import read_release_text


class ProjectReleaseTests(unittest.TestCase):
    def fixture(self, backend, tag="v1.2.3"):
        repository = backend.REPO.removeprefix("https://github.com")
        package = "CLIProxyAPI_1.2.3_windows_amd64.zip" if backend is cli else "cpa-manager-plus_v1.2.3_windows_amd64.zip"
        prefix = repository + "/releases/download/" + tag + "/"
        page = f'<a href="{repository}/releases/expanded_assets/{tag}">'
        assets = f'<a href="{prefix}{package}"><a href="{prefix}checksums.txt">'
        return page, assets

    def test_transient_attachment_failure_is_retried(self):
        for backend in (cli, plus):
            for error in (TimeoutError("timed out"), ConnectionResetError("connection reset"),
                          http.client.IncompleteRead(b"partial", 1024)):
                with self.subTest(backend=backend.REPO, error=type(error).__name__):
                    page, assets = self.fixture(backend)
                    with patch.object(backend, "windows_architecture", return_value="amd64"), \
                         patch.object(backend, "read_text", side_effect=[page, error, assets]) as read, \
                         patch("cpa_manager.core.network.time.sleep"):
                        release = backend.latest_release(None)
                    self.assertEqual(release[0], "v1.2.3")
                    self.assertEqual(read.call_count, 3)
                    self.assertEqual(read.call_args_list[1], read.call_args_list[2])

    def test_persistent_network_error_is_not_reported_as_missing_package(self):
        for backend in (cli, plus):
            with self.subTest(backend=backend.REPO):
                page, _ = self.fixture(backend)
                error = TimeoutError("read timed out")
                with patch.object(backend, "read_text", side_effect=[page, error, error]), \
                     patch("cpa_manager.core.network.time.sleep"):
                    with self.assertRaisesRegex(RuntimeError, "发布附件列表.*read timed out") as caught:
                        backend.latest_release(None)
                self.assertIs(caught.exception.__cause__, error)
                self.assertNotIn("ZIP", str(caught.exception))

    def test_network_error_does_not_fall_back_to_an_older_release(self):
        for backend in (cli, plus):
            with self.subTest(backend=backend.REPO):
                page, _ = self.fixture(backend)
                older, _ = self.fixture(backend, "v1.0.0")
                with patch.object(backend, "read_text", side_effect=[page + older, TimeoutError(), TimeoutError()]) as read, \
                     patch("cpa_manager.core.network.time.sleep"):
                    with self.assertRaisesRegex(RuntimeError, "发布附件列表"):
                        backend.latest_release(None, include_prerelease=True)
                self.assertEqual(read.call_count, 3)
                self.assertTrue(read.call_args.args[1].endswith("/v1.2.3"))

    def test_missing_checksum_is_reported_only_after_reading_attachment_list(self):
        for backend in (cli, plus):
            with self.subTest(backend=backend.REPO):
                page, assets = self.fixture(backend)
                assets = assets[:assets.index('<a href=', 1)]
                with patch.object(backend, "windows_architecture", return_value="amd64"), \
                     patch.object(backend, "read_text", side_effect=[page, assets]) as read:
                    with self.assertRaisesRegex(RuntimeError, "1 个 Windows amd64 ZIP 包、0 个 checksums.txt"):
                        backend.latest_release(None)
                self.assertEqual(read.call_count, 2)

    def test_links_from_another_release_are_not_selected(self):
        for backend in (cli, plus):
            with self.subTest(backend=backend.REPO):
                page, _ = self.fixture(backend)
                _, assets = self.fixture(backend, "v1.0.0")
                with patch.object(backend, "read_text", side_effect=[page, assets]):
                    with self.assertRaisesRegex(RuntimeError, "0 个 Windows.*0 个 checksums.txt"):
                        backend.latest_release(None)

    def test_release_page_read_is_also_retried(self):
        for backend in (cli, plus):
            with self.subTest(backend=backend.REPO):
                page, assets = self.fixture(backend)
                with patch.object(backend, "read_text", side_effect=[ConnectionResetError(), page, assets]), \
                     patch("cpa_manager.core.network.time.sleep"):
                    self.assertEqual(backend.latest_release(None)[0], "v1.2.3")

    def test_permanent_http_error_is_not_retried(self):
        error = HTTPError("https://github.com/test", 403, "Forbidden", {}, None)
        reader = Mock(side_effect=error)
        with patch("cpa_manager.core.network.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "发布页面.*403"):
                read_release_text(None, error.url, reader)
        reader.assert_called_once()
        sleep.assert_not_called()

    def test_transient_http_error_is_retried(self):
        error = HTTPError("https://github.com/test", 502, "Bad Gateway", {}, None)
        reader = Mock(side_effect=[error, "content"])
        with patch("cpa_manager.core.network.time.sleep"):
            self.assertEqual(read_release_text(None, error.url, reader), "content")
        self.assertEqual(reader.call_count, 2)
