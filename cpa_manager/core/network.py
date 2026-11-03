"""HTTP connections with explicit shared proxy settings."""
import urllib.parse
import urllib.request
import urllib.error
import http.client
import time
from cpa_manager.core.github_mirror import GitHubMirrorHandler, DownloadProxyHandler, RouteRedirectHandler, NetworkLogHandler, NetworkLogOpener


def network(proxy: str, user_agent="CLIProxyAPI-Updater/1.0"):
    mirror = getattr(proxy, "github_mirror", "")
    mirror_use_proxy = getattr(proxy, "mirror_use_proxy", False)
    report = getattr(proxy, "report", None)
    proxy = proxy.strip()
    if proxy:
        if "://" not in proxy:
            proxy = "http://" + proxy
        parsed = urllib.parse.urlsplit(proxy)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("请填写 HTTP/HTTPS 代理，例如 http://127.0.0.1:7890（不支持 SOCKS）。")
        try:
            if not parsed.port:
                raise ValueError()
        except ValueError:
            raise ValueError("代理地址需要有效端口，例如 http://127.0.0.1:7890。") from None
    # An empty setting means direct connection, without implicit system proxies.
    opener = urllib.request.build_opener(GitHubMirrorHandler(mirror), DownloadProxyHandler(proxy, mirror_use_proxy),
                                         RouteRedirectHandler(), NetworkLogHandler(proxy, mirror_use_proxy, report))
    opener.addheaders = [("User-Agent", user_agent)]
    return NetworkLogOpener(opener, report) if report else opener


def read_text(opener, url):
    with opener.open(url, timeout=45) as response:
        return response.read().decode("utf-8-sig")


def read_release_text(opener, url, reader):
    """Retry one transient read failure and preserve its cause for the user."""
    for attempt in range(2):
        try:
            return reader(opener, url)
        except (OSError, http.client.HTTPException) as error:
            retry = True
            if isinstance(error, urllib.error.HTTPError):
                retry = error.code in (408, 429, 500, 502, 503, 504)
                error.close()
            if attempt == 0 and retry:
                time.sleep(0.3)
                continue
            stage = "发布附件列表" if "/releases/expanded_assets/" in url else "发布页面"
            raise RuntimeError(f"读取 GitHub {stage}失败：{error}。请检查网络或代理后重试。") from error
