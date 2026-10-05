"""Shared GitHub release discovery, platform selection and verified downloads."""
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



def releases(value, proxy):
    repo = repository(value)
    opener = network(proxy)
    data = json.loads(read_text(opener, "https://api.github.com/repos/" +
                                           repo.split("github.com/")[1] + "/releases/latest"))
    return release_data(repo, data)



def release_catalog(value, proxy):
    """Fetch latest stable release and selectable published versions."""
    latest = releases(value, proxy)
    repo = latest["repository"]
    opener = network(proxy)
    data = json.loads(read_text(opener, "https://api.github.com/repos/" +
        repo.split("github.com/")[1] + "/releases?per_page=100"))
    result = [latest]
    seen = {latest["tag"]}
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
