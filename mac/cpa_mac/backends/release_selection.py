"""Release and asset selection for Mac software records."""
import json
import re
import urllib.parse

from cpa_mac.backends import github


def normalize_release(repo, data):
    if not isinstance(data, dict) or data.get("draft"):
        raise ValueError("发布信息无效或尚未公开。")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise ValueError("发布信息缺少版本号。")
    assets = []
    for item in data.get("assets") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"] and isinstance(item.get("browser_download_url"), str) and item["browser_download_url"]:
            assets.append({"name": item["name"], "url": item["browser_download_url"],
                           "size": item.get("size") if type(item.get("size")) is int else None,
                           "digest": item.get("digest") or ""})
    return {"repository": repo, "tag": tag, "prerelease": bool(data.get("prerelease")), "assets": assets}


def list_releases(value, proxy="", include_prerelease=False, page=1):
    if type(page) is not int or not 1 <= page <= 100:
        raise ValueError("发布列表页码无效。")
    repo = github.repository(value)
    url = f"https://api.github.com/repos/{github.owner_repo(repo)}/releases?per_page=30&page={page}"
    try:
        data = json.loads(github.read_text(github.network(proxy), url))
        if not isinstance(data, list):
            raise ValueError("发布列表格式无效。")
        releases = [normalize_release(repo, item) for item in data
                    if isinstance(item, dict) and not item.get("draft")
                    and (include_prerelease or not item.get("prerelease"))]
        return {"releases": [{"tag": item["tag"], "prerelease": item["prerelease"]} for item in releases],
                "page": page, "has_more": len(data) == 30}
    except Exception as error:
        if not github.is_rate_limited(error):
            raise RuntimeError("获取历史版本失败，请检查网络或代理。") from error
    text = github.read_text(github.network(proxy, accept="text/html"), f"{repo}/releases?page={page}")
    releases, seen = [], set()
    for section in re.findall(r"<section[^>]*>(.*?)</section>", text, re.DOTALL):
        match = re.search(r"/releases/tag/([^\"\s<>?#]+)", section)
        if not match:
            continue
        tag = urllib.parse.unquote(match.group(1))
        prerelease = bool(re.search(r"Pre-release", section, re.IGNORECASE))
        if tag not in seen and (include_prerelease or not prerelease):
            releases.append({"tag": tag, "prerelease": prerelease})
            seen.add(tag)
    has_more = bool(re.search(r"rel=[\"']next[\"']", text))
    if not releases and not has_more:
        raise RuntimeError("无法通过 GitHub 网页识别历史版本。")
    return {"releases": releases, "page": page, "has_more": has_more}


def fetch_release(value, tag, proxy="", include_prerelease=False, force=False):
    if not isinstance(tag, str) or len(tag) > 256 or any(ord(character) < 32 for character in tag):
        raise ValueError("目标版本号无效。")
    if not tag:
        return github.fetch_latest(value, proxy, include_prerelease=include_prerelease, force=force)
    repo = github.repository(value)
    url = f"https://api.github.com/repos/{github.owner_repo(repo)}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        release = normalize_release(repo, json.loads(github.read_text(github.network(proxy), url)))
        if release["tag"] != tag:
            raise ValueError("返回的版本与所选版本不一致。")
        return release
    except Exception as error:
        if not github.is_rate_limited(error):
            raise RuntimeError("无法获取所选版本，发布可能已删除；不会自动换成最新版。") from error
    assets = github.scrape_expanded_assets(repo, tag, github.network(proxy, accept="text/html"))
    if not assets:
        raise RuntimeError("所选版本没有可读取的附件，请检查网络或代理。")
    return {"repository": repo, "tag": tag, "prerelease": False, "assets": assets}


def compatible_assets(release, mode, architecture):
    return [asset for asset in release["assets"] if github.recommend([asset], mode, architecture)]


def resolve(record, mode, architecture, proxy="", include_prerelease=False, force=False, latest=None):
    tag = record.get("selected_tag") or ""
    release = latest if not tag and latest is not None else fetch_release(
        record["repository"], tag, proxy, include_prerelease, force)
    name = record.get("selected_asset") or ""
    candidates = compatible_assets(release, mode, architecture)
    asset = next((item for item in candidates if item["name"] == name), None) if name else github.recommend(
        candidates, mode, architecture)
    if asset is None:
        raise ValueError("所选版本/附件不存在或不适合当前 Mac 架构；请重新选择，不会静默替换附件。")
    return release, asset


def selection(record, mode, architecture, body, proxy="", include_prerelease=False):
    tag, name = body.get("tag", ""), body.get("asset", "")
    if not isinstance(tag, str) or not isinstance(name, str):
        raise ValueError("版本与附件必须是文本。")
    release, asset = resolve({**record, "selected_tag": tag, "selected_asset": name},
                             mode, architecture, proxy, include_prerelease, force=True)
    return {"selected_tag": release["tag"] if name else tag, "selected_asset": name, "asset": asset["name"]}
