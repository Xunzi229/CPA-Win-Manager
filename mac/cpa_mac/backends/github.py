"""GitHub release lookup, macOS asset selection and verified downloads."""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse

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


def fetch_latest(value, proxy=""):
    repo = repository(value)
    url = "https://api.github.com/repos/" + owner_repo(repo) + "/releases/latest"
    opener = network(proxy)
    try:
        data = json.loads(read_text(opener, url))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise RuntimeError("仓库没有可用的正式 Release。") from error
        if error.code in (403, 429):
            raise RuntimeError("GitHub 请求受限，请稍后重试。") from error
        raise RuntimeError(f"无法获取 Release（HTTP {error.code}）。") from error
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeError("无法获取 GitHub Release。") from error
    assets = []
    for item in data.get("assets") or []:
        name, download = item.get("name"), item.get("browser_download_url")
        if isinstance(name, str) and name and isinstance(download, str) and download:
            assets.append({"name": name, "url": download, "digest": item.get("digest") or "",
                           "size": item.get("size") if type(item.get("size")) is int else None})
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag or not assets:
        raise RuntimeError("此仓库的最新正式 Release 没有可下载附件。")
    return {"repository": repo, "tag": tag, "assets": assets}


def asset_pattern(spec, arch):
    token = spec["arch_token"][arch]
    return spec["asset_template"].format(arch=re.escape(token))


def choose_project_asset(assets, spec, arch):
    pattern = asset_pattern(spec, arch)
    matches = [asset for asset in assets if re.search(pattern, asset["name"], re.IGNORECASE)]
    preferred = [asset for asset in matches if "no-plugin" not in asset["name"].lower()]
    chosen = preferred or matches
    if len(chosen) != 1:
        raise RuntimeError(f"发布页面缺少唯一的 macOS {spec['arch_token'][arch]} 安装包。")
    return chosen[0]


def _detect_arch(name):
    arm = re.search(r"aarch64|arm64", name) is not None
    intel = re.search(r"amd64|x86_64|x64", name) is not None
    if arm and intel:
        return "conflict"
    if arm:
        return "arm64"
    if intel:
        return "amd64"
    if re.search(r"universal", name):
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


def download(url, destination, report, proxy="", cancel=None):
    opener = network(proxy, accept="*/*")
    failure = None
    for _attempt in range(2):
        destination.unlink(missing_ok=True)
        try:
            return _download(opener, url, destination, report, cancel)
        except urllib.error.URLError as error:
            failure = error
            destination.unlink(missing_ok=True)
    raise RuntimeError("下载失败，请检查网络或代理后重试。") from failure


def _download(opener, url, destination, report, cancel):
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


def download_verified(release, asset, destination, proxy, report, cancel=None):
    opener = network(proxy, accept="*/*")
    expected = expected_sha256(release, asset, opener)
    actual = download(asset["url"], destination, report, proxy, cancel)
    if expected and actual.lower() != expected.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("SHA256 校验失败，已删除下载文件。")
    return actual
