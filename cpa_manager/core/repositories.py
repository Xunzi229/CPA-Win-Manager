"""GitHub repository identity shared by installation records and backups."""
import re
import urllib.parse


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
