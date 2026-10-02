"""Parallel HTTP range downloads with persistent parts and cancellation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request

import cli_backend


class DownloadCancelled(Exception):
    pass


class DownloadControl(threading.Event):
    """Cancel or pause a live transfer without discarding open connections."""
    def __init__(self):
        super().__init__()
        self.paused = threading.Event()

    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()


class RangeUnsupported(Exception):
    pass


def check_cancel(cancel):
    if isinstance(cancel, DownloadControl):
        while cancel.paused.is_set() and not cancel.is_set():
            cancel.wait(0.1)
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled("下载已中断，缓存已保留；支持分段的附件可在下次继续下载。")


def size_text(size):
    if not isinstance(size, int) or size < 0:
        return "大小未知"
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} B"


def fetch(url, destination, report, proxy="", cancel=None, cache_root=None, identity="", workers=4):
    check_cancel(cancel)
    destination = Path(destination)
    cache_root = Path(cache_root or cli_backend.ROOT / ".download-cache")
    key = hashlib.sha256((url + "\n" + identity).encode()).hexdigest()
    cache = cache_root / key
    cache.mkdir(parents=True, exist_ok=True)
    # Reuse the cross-process lock to prevent simultaneous writes to the same parts.
    with cli_backend.update_lock(cache):
        opener = cli_backend.network(proxy)
        request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"})
        def connect(connection, request):
            for attempt in range(3):
                check_cancel(cancel)
                try:
                    return connection.open(request, timeout=10)
                except (OSError, http.client.HTTPException):
                    check_cancel(cancel)
                    if attempt == 2:
                        raise

        with connect(opener, request) as response:
            match = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("Content-Range", ""))
            ranged = response.status == 206 and match is not None
            total = int(match[1]) if ranged else int(response.headers.get("Content-Length") or 0)
            etag = response.headers.get("ETag", "")
            modified = response.headers.get("Last-Modified", "")
        check_cancel(cancel)
        validator = etag if etag and not etag.startswith("W/") else modified
        # Without a validator or immutable asset identity, reuse cannot be verified.
        resumable = ranged and bool(validator or identity)
        count = min(max(1, workers), max(1, total // (1024 * 1024))) if ranged else 1
        ranges = [(total * i // count, total * (i + 1) // count - 1) for i in range(count)]
        metadata = {"url": url, "identity": identity, "total": total,
                    "etag": etag, "modified": modified, "ranges": ranges}
        metadata_path = cache / "metadata.json"
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        # JSON normalizes tuples into lists.
        if previous != json.loads(json.dumps(metadata)) or not resumable:
            for part in cache.glob("*.part"):
                part.unlink()
        temporary = cache / "metadata.tmp"
        temporary.write_text(json.dumps(metadata), encoding="utf-8")
        os.replace(temporary, metadata_path)
        progress_lock = threading.Lock()
        amounts = [0] * count
        last_report = [0.0]
        started = time.monotonic()
        initial = [0]
        stopped = threading.Event()

        def progress(index, amount, force=False):
            with progress_lock:
                amounts[index] = amount
                now = time.monotonic()
                if not force and now - last_report[0] < 0.15:
                    return
                last_report[0] = now
                received = sum(amounts)
                speed = max(0, received - initial[0]) / max(now - started, 0.01)
                message = f"下载：{size_text(received)}"
                if total:
                    message += " / " + size_text(total)
                report(10 + 60 * received / total if total else None,
                       message + f" · {size_text(int(speed))}/s · {count} 线程")

        for i, (start, end) in enumerate(ranges):
            part = cache / f"{i}.part"
            amount = part.stat().st_size if part.exists() else 0
            if amount > end - start + 1 or not resumable:
                part.unlink(missing_ok=True)
                amount = 0
            amounts[i] = amount
        initial[0] = sum(amounts)
        report(10, f"使用 {count} 线程下载" + (f"，继续已下载的 {size_text(initial[0])}。" if initial[0] else "。"))

        def part_download(index):
            start, end = ranges[index]
            path = cache / f"{index}.part"
            try:
                for attempt in range(3):
                    check_cancel(cancel)
                    if stopped.is_set():
                        return
                    offset = path.stat().st_size if path.exists() else 0
                    if offset == end - start + 1:
                        return
                    headers = {"Range": f"bytes={start + offset}-{end}", "Accept-Encoding": "identity"}
                    if validator:
                        headers["If-Range"] = validator
                    try:
                        connection = cli_backend.network(proxy)
                        with connection.open(urllib.request.Request(url, headers=headers), timeout=10) as response:
                            expected = f"bytes {start + offset}-{end}/{total}"
                            if response.status != 206:
                                raise RangeUnsupported("服务器没有接受分段请求。")
                            if response.headers.get("Content-Range") != expected:
                                raise RuntimeError("服务器返回的下载范围不一致，已停止下载。")
                            if etag and response.headers.get("ETag") != etag:
                                raise RuntimeError("下载文件发生变化，请重新获取附件。")
                            if not etag and modified and response.headers.get("Last-Modified") != modified:
                                raise RuntimeError("下载文件发生变化，请重新获取附件。")
                            with path.open("ab") as output:
                                while True:
                                    check_cancel(cancel)
                                    if stopped.is_set():
                                        return
                                    chunk = response.read1(64 * 1024)
                                    if not chunk:
                                        break
                                    if offset + len(chunk) > end - start + 1:
                                        raise RuntimeError("下载分段长度异常。")
                                    output.write(chunk)
                                    offset += len(chunk)
                                    progress(index, offset)
                        if offset != end - start + 1:
                            raise OSError("下载分段不完整。")
                        progress(index, offset, True)
                        return
                    except (OSError, EOFError, http.client.HTTPException):
                        if attempt == 2:
                            raise
                raise RuntimeError("分段下载失败。")
            except Exception:
                stopped.set()
                raise

        if ranged:
            try:
                with ThreadPoolExecutor(max_workers=count) as executor:
                    futures = [executor.submit(part_download, i) for i in range(count)]
                    # Inspect every completed task; executor waits for all workers before cache reuse.
                    errors = []
                    for future in futures:
                        try:
                            future.result()
                        except Exception as error:
                            errors.append(error)
                check_cancel(cancel)
                if errors:
                    if all(isinstance(error, RangeUnsupported) for error in errors):
                        ranged = False
                    else:
                        raise errors[0]
            except DownloadCancelled:
                raise
        if not ranged:
            count = 1
            report(None, "服务器不支持分段下载，使用单线程；中断后需重新下载。")
            amounts[:] = [0] * count
            initial[0] = 0
            with connect(opener, urllib.request.Request(url, headers={"Accept-Encoding": "identity"})) as response, (cache / "single.part").open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                while True:
                    check_cancel(cancel)
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    progress(0, received)
                if total and received != total:
                    raise RuntimeError("下载不完整，请重试。")
            parts = [cache / "single.part"]
        else:
            parts = [cache / f"{i}.part" for i in range(count)]
        check_cancel(cancel)
        digest = hashlib.sha256()
        with destination.open("wb") as output:
            for path in parts:
                with path.open("rb") as source:
                    while chunk := source.read(256 * 1024):
                        check_cancel(cancel)
                        output.write(chunk)
                        digest.update(chunk)
        return digest.hexdigest()


def discard(url, identity="", cache_root=None):
    """Discard corrupt parts; callers only invoke this after fetch released its lock."""
    cache_root = Path(cache_root or cli_backend.ROOT / ".download-cache")
    key = hashlib.sha256((url + "\n" + identity).encode()).hexdigest()
    cache = cache_root / key
    if cache.exists():
        with cli_backend.update_lock(cache):
            for part in cache.glob("*.part"):
                part.unlink()
