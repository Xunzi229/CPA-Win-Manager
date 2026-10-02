"""HTTP connections with explicit shared proxy settings."""
import urllib.parse
import urllib.request


def network(proxy: str, user_agent="CLIProxyAPI-Updater/1.0"):
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {}))
    opener.addheaders = [("User-Agent", user_agent)]
    return opener


def read_text(opener, url):
    with opener.open(url, timeout=45) as response:
        return response.read().decode("utf-8-sig")
