"""Shared GitHub release discovery, platform selection and verified downloads."""
import html
import json
import re
import urllib.parse
import urllib.error
import threading
import time

from cpa_manager.core import download as transfer
from cpa_manager.core.network import network, read_text
from cpa_manager.core.runtime import windows_architecture


SEARCH_INTERVAL = 1.5
_search_lock = threading.Lock()
_last_search = float("-inf")


def repository(value):
    if not isinstance(value, str):
        raise ValueError("GitHub 地址必须是文本。")
    parsed = urllib.parse.urlsplit(value.strip())
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2])):
        raise ValueError("请填写 https://github.com/作者/仓库 或其 Releases 地址。")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if owner in (".", "..") or repo in ("", ".", ".."):
        raise ValueError("GitHub 仓库地址无效。")
    return f"https://github.com/{owner}/{repo}"


def search_repositories(query, proxy=""):
    global _last_search
    query = query.strip()
    if not query:
        return [], 0
    if len(query) > 256:
        raise ValueError("搜索关键词过长，请缩短后重试。")
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "per_page": 30})
    opener = network(proxy)
    # Shared by both software-library windows, including after closing and reopening.
    with _search_lock:
        remaining = SEARCH_INTERVAL - (time.monotonic() - _last_search)
        if remaining > 0:
            time.sleep(remaining)
        _last_search = time.monotonic()
    try:
        data = json.loads(read_text(opener, url))
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise RuntimeError("GitHub 搜索请求受限，请稍后重试。") from error
        raise
    entries, seen = [], set()
    for item in data.get("items", []):
        try:
            full_name = item["full_name"]
            address = repository("https://github.com/" + full_name)
        except (KeyError, TypeError, ValueError):
            continue
        identity = address.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        entries.append({"id": "github:" + identity, "name": full_name,
                        "description": item.get("description") or "暂无项目说明",
                        "repository": address, "categories": ["GitHub"]})
    return entries, data.get("total_count", len(entries))



CATALOG_CACHE_TTL = 600
_catalog_cache = {}
_cache_lock = threading.Lock()


def is_rate_limited(error):
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (403, 429):
            return True
    msg = str(error).lower()
    return "403" in msg or "rate limit" in msg or "rate_limit" in msg


def get_cached_catalog(repo, include_prerelease=False, max_age=CATALOG_CACHE_TTL):
    try:
        key = (repository(repo).lower(), bool(include_prerelease))
    except ValueError:
        return None
    with _cache_lock:
        entry = _catalog_cache.get(key)
        if entry:
            ts, catalog = entry
            if max_age is None or (time.time() - ts < max_age):
                if valid_catalog(catalog, repo):
                    return catalog
    return None


def set_cached_catalog(repo, catalog, include_prerelease=False, timestamp=None):
    if not valid_catalog(catalog, repo):
        return
    try:
        key = (repository(repo).lower(), bool(include_prerelease))
    except ValueError:
        return
    ts = timestamp if timestamp is not None else time.time()
    with _cache_lock:
        _catalog_cache[key] = (ts, catalog)


def clear_catalog_cache(repo=None):
    with _cache_lock:
        if repo is None:
            _catalog_cache.clear()
        else:
            try:
                rep_key = repository(repo).lower()
                for k in list(_catalog_cache):
                    if k[0] == rep_key:
                        _catalog_cache.pop(k, None)
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
    owner_repo = repo_url.removeprefix("https://github.com/").strip("/")
    url = f"https://github.com/{owner_repo}/releases/expanded_assets/{urllib.parse.quote(tag)}"
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
        if not asset_url_matches(url, repo_url):
            continue
        digest_m = re.search(r'sha256:([0-9a-fA-F]{64})', li)
        digest = digest_m.group(1).lower() if digest_m else ""
        size_m = re.search(r'>\s*([\d.]+\s*(?:[KMGTP]?B|Bytes))\s*</span>', li, re.IGNORECASE)
        size = parse_asset_size(size_m.group(1)) if size_m else None
        assets.append({"name": name, "url": url, "digest": digest, "size": size,
                       "id": None, "updated_at": None})
    return assets


def scrape_latest_release(value, proxy):
    repo = repository(value)
    owner_repo = repo.removeprefix("https://github.com/").strip("/")
    opener = network(proxy)
    releases_url = f"https://github.com/{owner_repo}/releases"
    try:
        html_text = read_text(opener, releases_url)
    except Exception:
        raise RuntimeError("无法通过网页获取 Release 列表。")

    tag_match = re.search(rf'/{re.escape(owner_repo)}/releases/tag/([^"\s<>/?#]+)', html_text) or \
                re.search(rf'/{re.escape(owner_repo)}/releases/expanded_assets/([^"\s<>]+)', html_text) or \
                re.search(r'/releases/tag/([^"\s<>/?#]+)', html_text)
    if not tag_match:
        raise RuntimeError("无法通过网页识别最新版本。")
    tag = tag_match.group(1)

    body_m = re.search(r'data-test-selector="body-content"[^>]*>(.*?)</div>', html_text, re.DOTALL) or re.search(r'class="markdown-body[^"]*"[^>]*>(.*?)</div>', html_text, re.DOTALL)
    notes = re.sub(r'<[^>]+>', '', body_m.group(1)).strip() if body_m else "暂无更新说明。"

    assets = scrape_expanded_assets(repo, tag, opener)
    if not assets:
        raise RuntimeError("此仓库的最新正式 Release 没有可下载附件。")
    return {"repository": repo, "tag": tag, "assets": assets, "notes": notes, "prerelease": False}


def scrape_release_catalog(value, proxy, include_prerelease=False, limit=10):
    repo = repository(value)
    owner_repo = repo.removeprefix("https://github.com/").strip("/")
    opener = network(proxy)
    releases_url = f"https://github.com/{owner_repo}/releases"
    content = read_text(opener, releases_url)

    sections = re.findall(r'<section[^>]*aria-labelledby="[^"]*"[^>]*>(.*?)</section>', content, re.DOTALL)
    if not sections:
        sections = re.findall(r'(<div[^>]*class="[^"]*release[^"]*"[^>]*>.*?)(?=<div[^>]*class="[^"]*release[^"]*"|$)', content, re.DOTALL)

    releases_info = []
    seen = set()
    if sections:
        for s in sections:
            tag_m = re.search(r'/releases/tag/([^"\s<>/?#]+)', s) or re.search(rf'/{re.escape(owner_repo)}/releases/expanded_assets/([^"\s<>]+)', s)
            if not tag_m:
                continue
            tag = tag_m.group(1)
            if tag in seen:
                continue
            seen.add(tag)
            is_pre = bool(re.search(r'Pre-release', s, re.IGNORECASE))
            body_m = re.search(r'data-test-selector="body-content"[^>]*>(.*?)</div>', s, re.DOTALL) or re.search(r'class="markdown-body[^"]*"[^>]*>(.*?)</div>', s, re.DOTALL)
            notes = re.sub(r'<[^>]+>', '', body_m.group(1)).strip() if body_m else "暂无更新说明。"
            releases_info.append((tag, is_pre, notes))
    else:
        tags_raw = re.findall(rf'/{re.escape(owner_repo)}/releases/expanded_assets/([^"\s<>]+)', content)
        for t in list(dict.fromkeys(tags_raw)):
            releases_info.append((t, False, "暂无更新说明。"))

    if not releases_info:
        raise RuntimeError("无法通过网页解析 Release 列表。")

    filtered = []
    for tag, is_pre, notes in releases_info:
        if not include_prerelease and is_pre:
            continue
        filtered.append((tag, is_pre, notes))
    if not filtered:
        filtered = releases_info

    result = []
    for tag, is_pre, notes in filtered[:limit]:
        try:
            assets = scrape_expanded_assets(repo, tag, opener)
            if assets:
                result.append({"repository": repo, "tag": tag, "assets": assets, "notes": notes, "prerelease": is_pre})
        except Exception:
            continue

    if not result:
        raise RuntimeError("此仓库没有可下载附件的 Release。")

    if include_prerelease and len(result) > 1:
        from cpa_manager.backends.cli import version_key
        best = max(result, key=lambda r: (version_key(r["tag"]) or ((0, 0, 0), False, ())))
        if best is not result[0]:
            result.remove(best)
            result.insert(0, best)
    return result


def releases(value, proxy):
    repo = repository(value)
    opener = network(proxy)
    try:
        data = json.loads(read_text(opener, "https://api.github.com/repos/" +
                                               repo.split("github.com/")[1] + "/releases/latest"))
        return release_data(repo, data)
    except Exception as error:
        if is_rate_limited(error):
            return scrape_latest_release(repo, proxy)
        raise


def release_catalog(value, proxy, include_prerelease=False, max_age=CATALOG_CACHE_TTL, force=False):
    """Fetch latest stable release and selectable published versions."""
    repo = repository(value)
    if not force and max_age is not None:
        cached = get_cached_catalog(repo, include_prerelease=include_prerelease, max_age=max_age)
        if cached:
            return cached

    result = []
    seen = set()
    latest_failed_403 = False
    try:
        latest = releases(value, proxy)
        result = [latest]
        seen = {latest["tag"]}
    except Exception as error:
        if is_rate_limited(error):
            latest_failed_403 = True
        elif not include_prerelease:
            raise
        latest = None
        result = []
        seen = set()

    if latest_failed_403:
        catalog = scrape_release_catalog(repo, proxy, include_prerelease=include_prerelease)
        set_cached_catalog(repo, catalog, include_prerelease=include_prerelease)
        return catalog

    opener = network(proxy)
    try:
        data = json.loads(read_text(opener, "https://api.github.com/repos/" +
            repo.split("github.com/")[1] + "/releases?per_page=100"))
    except Exception as error:
        if is_rate_limited(error):
            catalog = scrape_release_catalog(repo, proxy, include_prerelease=include_prerelease)
            set_cached_catalog(repo, catalog, include_prerelease=include_prerelease)
            return catalog
        raise

    for item in data:
        if item.get("draft") or item.get("tag_name") in seen:
            continue
        try:
            release = release_data(repo, item)
        except RuntimeError:
            continue
        release["prerelease"] = bool(item.get("prerelease"))
        result.append(release)
        seen.add(release["tag"])
    if not result:
        raise RuntimeError("此仓库没有可下载附件的 Release。")
    if include_prerelease and len(result) > 1:
        from cpa_manager.backends.cli import version_key
        best = max(result, key=lambda r: (version_key(r["tag"]) or ((0, 0, 0), False, ())))
        if best is not result[0]:
            result.remove(best)
            result.insert(0, best)

    set_cached_catalog(repo, result, include_prerelease=include_prerelease)
    return result



def asset_url_matches(url, repo):
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    parts = parsed.path.split("/")
    expected = urllib.parse.urlsplit(repository(repo)).path.split("/")
    return (parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
            and len(parts) >= 6 and [p.lower() for p in parts[1:3]] == [p.lower() for p in expected[1:3]]
            and parts[3:5] == ["releases", "download"])


def valid_release(value, repo=None):
    if not isinstance(value, dict):
        return False
    try:
        source = repository(value.get("repository"))
        if repo and source.lower() != repository(repo).lower():
            return False
    except ValueError:
        return False
    return (isinstance(value.get("tag"), str) and bool(value["tag"])
            and isinstance(value.get("notes"), str)
            and isinstance(value.get("assets"), list)
            and all(isinstance(a, dict) and isinstance(a.get("name"), str) and bool(a["name"])
                    and asset_url_matches(a.get("url"), source)
                    and (a.get("size") is None or type(a["size"]) is int and a["size"] >= 0)
                    and isinstance(a.get("digest", ""), str)
                    for a in value["assets"]))


def valid_catalog(value, repo):
    return isinstance(value, list) and bool(value) and all(valid_release(r, repo) for r in value)


def release_data(repo, data):
    assets = []
    for item in data.get("assets", []):
        name, url = item.get("name", ""), item.get("browser_download_url", "")
        if name and asset_url_matches(url, repo):
            assets.append({"name": name, "url": url, "digest": item.get("digest") or "",
                           "size": item.get("size"), "id": item.get("id"),
                           "updated_at": item.get("updated_at")})
    if not data.get("tag_name") or not assets:
        raise RuntimeError("此仓库的最新正式 Release 没有可下载附件。")
    return {"repository": repo, "tag": data["tag_name"], "assets": assets,
            "notes": data.get("body") or "暂无更新说明。"}



def asset_score(asset, mode, native):
    name = asset["name"].lower()
    stem = name.rsplit(".", 1)[0]
    tokens = set(re.split(r"[-_\s()]+", stem))
    def marked(pattern):
        return re.search(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", stem) is not None
    foreign = marked(r"linux|darwin|macos|osx|android|freebsd|ubuntu|unix")
    windows = marked(r"windows|win|win32|win64") or name.endswith((".exe", ".msi"))
    if marked(r"arm64|aarch64|armv8a?"):
        architecture = "arm64"
    elif marked(r"amd64|x64|x86[_-]64|intel64|win64") or tokens & {"64", "amd"}:
        architecture = "amd64"
    elif marked(r"x86|i[3-6]86|ia32|x32|win32") or tokens & {"86", "32"}:
        architecture = "x86"
    elif marked(r"arm|armv7|armhf"):
        architecture = "arm32"
    else:
        architecture = None
    compatible = architecture in (None, native) or (native in ("amd64", "arm64") and architecture == "x86")
    architecture_rank = 0 if architecture == native else 1 if architecture is None else 2
    installer = marked(r"setup|installer|install") or name.endswith(".msi")
    package_rank = (0 if installer else 1) if mode == "安装器" else (
        2 if installer else 0 if name.endswith(".zip") else 1)
    return (int(foreign or not compatible), int(not windows), architecture_rank, package_rank, name)



def candidates(release, mode="便携安装"):
    suffixes = (".exe", ".msi") if mode == "安装器" else (".zip", ".exe")
    arch = windows_architecture()
    return sorted([a for a in release["assets"] if a["name"].lower().endswith(suffixes)],
                  key=lambda asset: asset_score(asset, mode, arch))



def recommended_asset(assets, mode="便携安装"):
    """Select a compatible Windows candidate; leave known mismatches unselected."""
    if not assets:
        return None
    native = windows_architecture()
    selected = min(assets, key=lambda asset: asset_score(asset, mode, native))
    return selected if asset_score(selected, mode, native)[0] == 0 else None



def asset_identity(asset):
    return json.dumps([asset.get("id"), asset.get("updated_at"), asset.get("digest"),
                       asset.get("size")]) if asset.get("id") or asset.get("digest") else ""



def download(release, asset, destination, proxy, report, cancel=None, workers=None):
    if asset not in release["assets"]:
        raise ValueError("附件不属于当前 Release。")
    opener = network(proxy)
    identity = asset_identity(asset)
    if workers is not None:
        digest = transfer.fetch(asset["url"], destination, report, proxy=proxy,
                                cancel=cancel, identity=identity, workers=workers)
    else:
        digest = transfer.fetch(asset["url"], destination, report, proxy=proxy,
                                cancel=cancel, identity=identity)
    transfer.check_cancel(cancel)
    expected = None
    if asset.get("digest"):
        match = re.fullmatch(r"sha256:([a-fA-F0-9]{64})", asset["digest"])
        if match:
            expected = match[1]
    if expected is None:
        sums = [a for a in release["assets"] if a["name"].lower() in
                ("checksums.txt", "sha256sums.txt", "sha256sums", "sha256sum.txt")]
        if len(sums) == 1:
            text = read_text(opener, sums[0]["url"])
            values = re.findall(r"^([a-fA-F0-9]{64})\s+\*?(?:\./)?" +
                                re.escape(asset["name"]) + r"\s*$", text, re.MULTILINE)
            if len(values) != 1:
                raise RuntimeError("校验文件缺少所选附件的唯一 SHA256。")
            expected = values[0]
    if expected and digest.lower() != expected.lower():
        transfer.discard(asset["url"], identity)
        raise RuntimeError("SHA256 校验失败，已取消安装。")
    transfer.check_cancel(cancel)
    report(75, "SHA256 校验通过。" if expected else "发布者未提供可识别的 SHA256，已完成下载。")
