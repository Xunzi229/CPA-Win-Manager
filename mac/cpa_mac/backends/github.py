"""GitHub release lookup, macOS asset selection, caching, rate-limit web fallback and verified downloads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cpa_mac.core.network import network, read_text


ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".zip")
PACKAGE_SUFFIXES = (".dmg", ".pkg")
IGNORED_SUFFIXES = (".sha256", ".sha256sum", ".sig", ".asc", ".digest", ".sbom",
                    ".json", ".txt", ".md", ".sum", ".blockmap", ".yml", ".yaml")


def repository(value):
    if not isinstance(value, str):
        raise ValueError("GitHub 地址必须是文本。")
    text = value.strip()
    if "://" not in text and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        text = "https://github.com/" + text
    parsed = urllib.parse.urlsplit(text)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts[:2])):
        raise ValueError("请填写 https://github.com/作者/仓库 或其 Releases 地址。")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if owner in (".", "..") or repo in ("", ".", ".."):
        raise ValueError("GitHub 仓库地址无效。")
    return f"https://github.com/{owner}/{repo}"


def owner_repo(value):
    return repository(value).removeprefix("https://github.com/")


_search_lock = threading.Lock()
_last_search = float("-inf")


def search_repositories(query, proxy=""):
    global _last_search
    query = query.strip()
    if not query:
        return [], 0
    if len(query) > 256:
        raise ValueError("搜索关键词过长，请缩短后重试。")
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "per_page": 30})
    opener = network(proxy)
    with _search_lock:
        remaining = 1.5 - (time.monotonic() - _last_search)
        if remaining > 0:
            time.sleep(remaining)
        _last_search = time.monotonic()
    try:
        data = json.loads(read_text(opener, url))
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise RuntimeError("GitHub 搜索请求受限，请稍后重试。") from error
        raise RuntimeError(f"GitHub 搜索失败（HTTP {error.code}）。") from error
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeError("无法搜索 GitHub。") from error
    entries, seen = [], set()
    for item in data.get("items") or []:
        full_name = item.get("full_name") if isinstance(item, dict) else ""
        try:
            address = repository("https://github.com/" + full_name)
        except (TypeError, ValueError):
            continue
        if address.casefold() in seen:
            continue
        seen.add(address.casefold())
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        entries.append({"name": address.rsplit("/", 1)[-1], "full_name": owner_repo(address),
                        "description": description or "暂无项目说明", "repository": address})
    total = data.get("total_count")
    return entries, total if type(total) is int else len(entries)


CATALOG_CACHE_TTL = 600
_release_cache = {}
_cache_lock = threading.Lock()


def is_rate_limited(error):
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (403, 429):
            return True
    msg = str(error).lower()
    return "403" in msg or "rate limit" in msg or "rate_limit" in msg


def get_cached_release(repo, include_prerelease=False, max_age=CATALOG_CACHE_TTL):
    try:
        key = (repository(repo).lower(), bool(include_prerelease))
    except ValueError:
        return None
    with _cache_lock:
        entry = _release_cache.get(key)
        if entry:
            ts, release = entry
            if max_age is None or (time.time() - ts < max_age):
                return release
    return None


def set_cached_release(repo, release, include_prerelease=False, timestamp=None):
    if not isinstance(release, dict) or not release.get("tag") or not release.get("assets"):
        return
    try:
        key = (repository(repo).lower(), bool(include_prerelease))
    except ValueError:
        return
    ts = timestamp if timestamp is not None else time.time()
    with _cache_lock:
        _release_cache[key] = (ts, release)


def clear_release_cache(repo=None):
    with _cache_lock:
        if repo is None:
            _release_cache.clear()
        else:
            try:
                rep_key = repository(repo).lower()
                for k in list(_release_cache):
                    if k[0] == rep_key:
                        _release_cache.pop(k, None)
            except ValueError:
                pass


def parse_asset_size(size_str):
    if not size_str:
        return None
    m = re.match(r'([\d.]+)\s*([A-Za-z]+)', size_str.strip())
    if not m:
        return None
    try:
        val = float(m[1])
        unit = m[2].upper()
        units = {'B': 1, 'BYTES': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
        return int(val * units.get(unit, 1))
    except (ValueError, TypeError):
        return None


def scrape_expanded_assets(repo, tag, opener):
    repo_url = repository(repo)
    owner_rp = owner_repo(repo_url)
    url = f"https://github.com/{owner_rp}/releases/expanded_assets/{urllib.parse.quote(tag)}"
    try:
        content = read_text(opener, url)
    except Exception:
        return []
    items = re.findall(r'<li[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</li>', content, re.DOTALL)
    assets = []
    for li in items:
        m = re.search(r'href="(?P<path>/[^"]+/releases/download/[^"]+/(?P<name>[^"]+))"', li)
        if not m:
            continue
        path = m.group('path')
        name = html.unescape(m.group('name'))
        url = "https://github.com" + path
        digest_m = re.search(r'sha256:([0-9a-fA-F]{64})', li)
        digest = digest_m.group(1).lower() if digest_m else ""
        size_m = re.search(r'>\s*([\d.]+\s*(?:[KMGTP]?B|Bytes))\s*</span>', li, re.IGNORECASE)
        size = parse_asset_size(size_m.group(1)) if size_m else None
        assets.append({"name": name, "url": url, "digest": digest, "size": size})
    return assets


def scrape_latest_release(value, proxy="", include_prerelease=False):
    repo = repository(value)
    owner_rp = owner_repo(repo)
    opener = network(proxy)
    releases_url = f"https://github.com/{owner_rp}/releases"
    try:
        html_text = read_text(opener, releases_url)
    except Exception:
        raise RuntimeError("无法通过网页获取 Release 列表。")

    sections = re.findall(r'<section[^>]*>(.*?)</section>', html_text, re.DOTALL)
    if not sections:
        sections = re.findall(r'(<div[^>]*class="[^"]*release[^"]*"[^>]*>.*?)(?=<div[^>]*class="[^"]*release[^"]*"|$)', html_text, re.DOTALL)
    releases_info = []
    seen = set()
    if sections:
        for s in sections:
            tag_m = re.search(r'/releases/tag/([^"\s<>/?#]+)', s) or re.search(rf'/{re.escape(owner_rp)}/releases/expanded_assets/([^"\s<>]+)', s)
            if not tag_m:
                continue
            tag = tag_m.group(1)
            if tag in seen:
                continue
            seen.add(tag)
            is_pre = bool(re.search(r'Pre-release', s, re.IGNORECASE))
            releases_info.append((tag, is_pre))
    else:
        tags_raw = re.findall(rf'/{re.escape(owner_rp)}/releases/expanded_assets/([^"\s<>]+)', html_text)
        for t in list(dict.fromkeys(tags_raw)):
            releases_info.append((t, False))

    filtered = [item for item in releases_info if include_prerelease or not item[1]]
    if not filtered:
        filtered = releases_info
    if not filtered:
        raise RuntimeError("无法通过网页识别最新版本。")

    tag, is_pre = filtered[0]
    assets = scrape_expanded_assets(repo, tag, opener)
    if not assets:
        raise RuntimeError("此仓库没有可下载附件的 Release。")
    return {"repository": repo, "tag": tag, "assets": assets, "prerelease": is_pre}


def fetch_latest(value, proxy="", include_prerelease=False, force=False):
    repo = repository(value)
    if not force:
        cached = get_cached_release(repo, include_prerelease=include_prerelease)
        if cached:
            return cached

    opener = network(proxy)
    endpoint = f"https://api.github.com/repos/{owner_repo(repo)}/releases?per_page=10" if include_prerelease else f"https://api.github.com/repos/{owner_repo(repo)}/releases/latest"
    try:
        data = json.loads(read_text(opener, endpoint))
    except Exception as error:
        if is_rate_limited(error):
            release = scrape_latest_release(repo, proxy=proxy, include_prerelease=include_prerelease)
            set_cached_release(repo, release, include_prerelease=include_prerelease)
            return release
        if isinstance(error, urllib.error.HTTPError) and error.code == 404:
            raise RuntimeError("仓库没有可用的正式 Release。") from error
        raise RuntimeError(f"无法获取 GitHub Release（{error}）。") from error

    target_item = None
    if include_prerelease and isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and not it.get("draft"):
                target_item = it
                break
    elif isinstance(data, dict):
        target_item = data

    if not target_item:
        raise RuntimeError("仓库没有可用的 Release。")

    assets = []
    for item in target_item.get("assets") or []:
        name, download_url = item.get("name"), item.get("browser_download_url")
        if isinstance(name, str) and name and isinstance(download_url, str) and download_url:
            assets.append({"name": name, "url": download_url, "digest": item.get("digest") or "",
                           "size": item.get("size") if type(item.get("size")) is int else None})
    tag = target_item.get("tag_name")
    if not isinstance(tag, str) or not tag or not assets:
        raise RuntimeError("此仓库的最新 Release 没有可下载附件。")
    res = {"repository": repo, "tag": tag, "assets": assets, "prerelease": bool(target_item.get("prerelease", False))}
    set_cached_release(repo, res, include_prerelease=include_prerelease)
    return res


def asset_pattern(spec, arch):
    token = spec["arch_token"][arch]
    return spec["asset_template"].format(arch=re.escape(token))


def choose_project_asset(assets, spec, arch):
    pattern = asset_pattern(spec, arch)
    matches = [asset for asset in assets if re.search(pattern, asset["name"], re.I)]
    if len(matches) != 1:
        raise RuntimeError(f"找不到匹配当前 Mac 架构（{arch}）的唯一安装包。")
    return matches[0]


def _detect_arch(lower):
    arm = re.search(r"aarch64|arm64", lower) is not None
    intel = re.search(r"x86_64|amd64|x64", lower) is not None
    universal = "universal" in lower
    if sum((arm, intel, universal)) > 1:
        return "conflict"
    if arm:
        return "arm64"
    if intel:
        return "amd64"
    if universal:
        return "universal"
    return None


def asset_role(name):
    lower = name.lower()
    if lower.endswith(IGNORED_SUFFIXES):
        return None
    if lower.endswith(PACKAGE_SUFFIXES):
        return "package"
    if lower.endswith(ARCHIVE_SUFFIXES):
        return "portable"
    filename = lower.rsplit("/", 1)[-1]
    if "." not in filename:
        return "binary"
    return None


def recommend(assets, mode, native):
    ranked = []
    for asset in assets:
        role = asset_role(asset["name"])
        if mode == "package":
            if role != "package":
                continue
        elif role not in ("portable", "binary"):
            continue
        score = _score(asset["name"], native, mode)
        if score is None:
            continue
        ranked.append((score, asset["name"].lower(), asset))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _score(name, native, mode):
    lower = name.lower()
    mac = re.search(r"darwin|macos|osx|apple", lower) is not None
    foreign = re.search(r"windows|win32|win64|(?<![a-z0-9])linux(?![a-z0-9])|freebsd|android", lower) is not None
    if foreign and not mac:
        return None
    arch = _detect_arch(lower)
    if arch == "conflict" or arch not in (None, "universal", native):
        return None
    mac_rank = 0 if mac else 1
    arch_rank = 0 if arch == native else 1 if arch == "universal" else 2
    if mode == "portable":
        kind_rank = 0 if asset_role(name) == "portable" else 1
    else:
        kind_rank = 0 if lower.endswith(".dmg") else 1
    return mac_rank, arch_rank, kind_rank


def expected_sha256(release, asset, opener):
    match = re.fullmatch(r"sha256:([a-fA-F0-9]{64})", asset.get("digest") or "")
    if match:
        return match[1]
    sums = [item for item in release["assets"] if item["name"].lower() in
            ("checksums.txt", "sha256sums.txt", "sha256sums")]
    sidecar = [item for item in release["assets"]
               if item["name"].lower() in (asset["name"].lower() + ".sha256", asset["name"].lower() + ".sha256sum")]
    if len(sums) == 1:
        text = read_text(opener, sums[0]["url"])
    elif len(sidecar) == 1:
        text = read_text(opener, sidecar[0]["url"])
    else:
        return None
    values = re.findall(r"(?im)^([a-fA-F0-9]{64})\s+\*?(?:\./)?" + re.escape(asset["name"]) + r"\s*$", text)
    if len(values) != 1:
        values = re.findall(r"(?im)^(?:SHA256\s*\(|)([a-fA-F0-9]{64})(?:\)\s*=\s*|\s+)", text)
    if len(values) != 1:
        bare = re.findall(r"(?im)^([a-fA-F0-9]{64})\s*$", text.strip())
        if len(bare) == 1:
            return bare[0]
        raise RuntimeError("校验文件缺少所选附件的唯一 SHA256。")
    return values[0]


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download_stream(opener, url, destination, report, cancel):
    digest = hashlib.sha256()
    with opener.open(url, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        received, last = 0, 0.0
        while chunk := response.read(256 * 1024):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("任务已取消。")
            output.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            now = time.monotonic()
            if now - last >= 0.1:
                size = f"{received / 1048576:.1f} MB"
                if total:
                    size += f" / {total / 1048576:.1f} MB"
                report(10 + 60 * received / total if total else None, "正在下载：" + size)
                last = now
        if total and received != total:
            raise RuntimeError("下载不完整，请重试。")
    return digest.hexdigest()


def _download_chunked(opener, url, destination, total_size, workers, report, cancel):
    chunk_size = (total_size + workers - 1) // workers
    parts = []
    for i in range(workers):
        start = i * chunk_size
        end = min(total_size - 1, (i + 1) * chunk_size - 1)
        if start <= end:
            parts.append((start, end))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as f:
        f.truncate(total_size)

    lock = threading.Lock()
    received = 0
    last_report = 0.0

    def download_range(start, end):
        nonlocal received, last_report
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
        with opener.open(req, timeout=60) as resp, destination.open("r+b") as out:
            out.seek(start)
            while chunk := resp.read(128 * 1024):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("任务已取消。")
                out.write(chunk)
                with lock:
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 0.1:
                        size = f"{received / 1048576:.1f} MB / {total_size / 1048576:.1f} MB"
                        report(10 + 60 * received / total_size, f"正在并发下载（{len(parts)} 分块）：" + size)
                        last_report = now

    with ThreadPoolExecutor(max_workers=len(parts)) as pool:
        futures = [pool.submit(download_range, s, e) for s, e in parts]
        for f in futures:
            f.result()

    return file_sha256(destination)


def download(url, destination, report, proxy="", cancel=None, workers=4):
    opener = network(proxy, accept="*/*")
    failure = None
    workers = max(1, min(16, int(workers) if workers else 4))
    for _attempt in range(2):
        destination.unlink(missing_ok=True)
        try:
            # 探测 Range 支持与文件大小
            if workers > 1:
                try:
                    probe_req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
                    with opener.open(probe_req, timeout=20) as probe_resp:
                        cr = probe_resp.headers.get("Content-Range", "")
                        total_m = re.search(r"/(\d+)$", cr)
                        if probe_resp.status == 206 and total_m:
                            total = int(total_m.group(1))
                            if total >= 2 * 1024 * 1024:
                                return _download_chunked(opener, url, destination, total, workers, report, cancel)
                except Exception:
                    pass
            return _download_stream(opener, url, destination, report, cancel)
        except urllib.error.URLError as error:
            failure = error
            destination.unlink(missing_ok=True)
    raise RuntimeError("下载失败，请检查网络或代理后重试。") from failure


def download_verified(release, asset, destination, proxy, report, cancel=None, workers=4):
    opener = network(proxy, accept="*/*")
    expected = expected_sha256(release, asset, opener)
    actual = download(asset["url"], destination, report, proxy, cancel, workers=workers)
    if expected and actual.lower() != expected.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("SHA256 校验失败，已删除下载文件。")
    return actual
