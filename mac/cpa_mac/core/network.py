"""HTTP connections with an explicit proxy."""
import urllib.parse
import urllib.request


def network(proxy: str, user_agent="CPA-Mac-Manager/1.0", accept="application/vnd.github+json"):
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {}))
    opener.addheaders = [("User-Agent", user_agent), ("Accept", accept)]
    return opener


def read_text(opener, url):
    with opener.open(url, timeout=45) as response:
        return response.read().decode("utf-8-sig")
