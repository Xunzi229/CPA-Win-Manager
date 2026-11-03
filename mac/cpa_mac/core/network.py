"""HTTP connections with an explicit proxy."""
import urllib.parse
import urllib.request
from cpa_mac.core.github_mirror import GitHubMirrorHandler, DownloadProxyHandler, RouteRedirectHandler, NetworkLogHandler, NetworkLogOpener


def network(proxy: str, user_agent="CPA-Mac-Manager/1.0", accept="application/vnd.github+json"):
    mirror = getattr(proxy, "github_mirror", "")
    mirror_use_proxy = getattr(proxy, "mirror_use_proxy", False)
    report = getattr(proxy, "report", None)
    proxy = (proxy or "").strip()
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
    opener = urllib.request.build_opener(GitHubMirrorHandler(mirror), DownloadProxyHandler(proxy, mirror_use_proxy),
                                         RouteRedirectHandler(), NetworkLogHandler(proxy, mirror_use_proxy, report))
    opener.addheaders = [("User-Agent", user_agent), ("Accept", accept)]
    return NetworkLogOpener(opener, report) if report else opener


def read_text(opener, url):
    with opener.open(url, timeout=45) as response:
        return response.read().decode("utf-8-sig")
