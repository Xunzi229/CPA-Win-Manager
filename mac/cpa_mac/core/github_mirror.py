"""Per-operation GitHub download routing, independent of the HTTP proxy."""
import urllib.parse
import urllib.request
import urllib.error
import functools
import inspect
import itertools
import time
import http.client


MIRROR_SOURCES = {
    "GitHub 官方源": "",
    "GitHub 镜像 · gh-proxy.com": "https://gh-proxy.com/",
    "GitHub 镜像 · ghfast.top": "https://ghfast.top/",
}


def normalize_mirror(value):
    if not isinstance(value, str):
        raise ValueError("请填写 GitHub 反代地址。")
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and "?" not in value and "#" not in value
                 and not any(char.isspace() or char == "\\" for char in value)
                 and port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请填写有效的 HTTP/HTTPS 反代地址，例如 https://gh-proxy.com/，不含账号、查询参数或片段。")
    return value.rstrip("/") + "/"


class NetworkRoute(str):
    """Keep the existing proxy-string API while carrying a download source.

    Each task captures its route; concurrent tasks and range workers never rely
    on process-wide mutable settings. The string value remains the HTTP proxy.
    """
    def __new__(cls, proxy="", github_mirror="", mirror_use_proxy=False, report=None):
        route = super().__new__(cls, proxy)
        route.github_mirror = normalize_mirror(github_mirror)
        route.mirror_use_proxy = bool(mirror_use_proxy)
        route.report = report
        return route


def safe_url(value):
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port:
        host += f":{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", "")) + ("?[已隐藏参数]" if parsed.query else "")


def logged_route(proxy, report):
    report = getattr(proxy, "report", None) or report
    route = NetworkRoute(str(proxy), getattr(proxy, "github_mirror", ""),
                         getattr(proxy, "mirror_use_proxy", False), report)
    official = safe_url(str(route)) if route else "直连"
    mirror = safe_url(route.github_mirror) if route.github_mirror else "GitHub 官方源"
    mirror_proxy = official if route.mirror_use_proxy else "直连"
    report(None, f"网络设置：官方源/版本查询 HTTP 代理={official}；下载源={mirror}；镜像 HTTP 代理={mirror_proxy}。")
    return route


def download_fallback(operation):
    """Restart a refused mirror download through the same HTTP proxy.

    Retry the complete verified operation, so range validators and checksums
    are read again rather than mixing responses from two download sources.
    Metadata errors, cancellation and integrity failures are never retried here.
    """
    signature = inspect.signature(operation)

    @functools.wraps(operation)
    def run(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        proxy = bound.arguments.get("proxy", "")
        report = bound.arguments["report"]
        proxy = logged_route(proxy, report)
        bound.arguments["proxy"] = proxy
        mirror = getattr(proxy, "github_mirror", "")
        try:
            return operation(*bound.args, **bound.kwargs)
        except urllib.error.HTTPError as error:
            if (not mirror or not error.geturl().startswith(mirror)
                    or error.code not in (401, 403, 404, 408, 410, 429, 500, 502, 503, 504)):
                raise
            code = error.code
            error.close()
        report(None, f"GitHub 镜像 {urllib.parse.urlsplit(mirror).hostname} 返回 HTTP {code}，正在回退 GitHub 官方源（保留当前 HTTP/HTTPS 代理）…")
        bound.arguments["proxy"] = logged_route(NetworkRoute(str(proxy), report=proxy.report), report)
        try:
            return operation(*bound.args, **bound.kwargs)
        except urllib.error.HTTPError as error:
            detail = f"HTTP {error.code}"
            error.close()
            raise RuntimeError(f"镜像下载失败（HTTP {code}），回退 GitHub 官方源仍失败（{detail}）。请检查网络代理或更换下载源。") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"镜像下载失败（HTTP {code}），回退 GitHub 官方源仍无法连接。请检查网络代理或更换下载源。") from error

    return run


def mirror_url(url, mirror):
    if not mirror:
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        return url
    if parsed.hostname == "github.com":
        parts = parsed.path.split("/")
        downloadable = (len(parts) >= 6 and parts[3] == "releases"
                        and (parts[4] == "download" or parts[4:6] == ["latest", "download"]))
        downloadable = downloadable or (len(parts) >= 5 and parts[3] == "archive")
    else:
        downloadable = parsed.hostname == "raw.githubusercontent.com"
    return mirror + url if downloadable else url


class GitHubMirrorHandler(urllib.request.BaseHandler):
    # Rewrite before urllib adds Host and before ProxyHandler selects a proxy.
    handler_order = 100

    def __init__(self, mirror):
        self.mirror = normalize_mirror(mirror)

    def https_request(self, request):
        target = mirror_url(request.full_url, self.mirror)
        if target != request.full_url:
            request.full_url = target
            request.remove_header("Host")
            request.github_mirror_download = True
        return request

    http_request = https_request


class DownloadProxyHandler(urllib.request.ProxyHandler):
    def __init__(self, proxy, mirror_use_proxy=False):
        self.mirror_use_proxy = mirror_use_proxy
        super().__init__({"http": proxy, "https": proxy} if proxy else {})

    def proxy_open(self, request, proxy, protocol):
        if getattr(request, "github_mirror_download", False) and not self.mirror_use_proxy:
            return None
        return super().proxy_open(request, proxy, protocol)


class RouteRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected is not None and getattr(request, "github_mirror_download", False):
            redirected.github_mirror_download = True
        return redirected


_request_ids = itertools.count(1)


class NetworkLogHandler(urllib.request.BaseHandler):
    handler_order = 450

    def __init__(self, proxy, mirror_use_proxy=False, report=None):
        self.proxy, self.mirror_use_proxy, self.report = proxy, mirror_use_proxy, report

    def https_request(self, request):
        if self.report:
            request.network_log_id = next(_request_ids)
            request.network_started = time.monotonic()
            mirrored = getattr(request, "github_mirror_download", False)
            configured = self.proxy if not mirrored or self.mirror_use_proxy else ""
            active = configured if configured and not urllib.request.proxy_bypass(request.host) else ""
            proxy = safe_url(active) if active else "直连"
            source = "GitHub 镜像" if mirrored else "GitHub 官方/原始地址"
            byte_range = request.get_header("Range") or "完整文件"
            self.report(None, f"网络请求 #{request.network_log_id}：{request.get_method()} {safe_url(request.full_url)} | 来源={source} | HTTP 代理={proxy} | Range={byte_range}")
        return request

    http_request = https_request

    def https_response(self, request, response):
        if self.report:
            seconds = time.monotonic() - getattr(request, "network_started", time.monotonic())
            length = response.headers.get("Content-Length", "未知")
            byte_range = response.headers.get("Content-Range", "无")
            self.report(None, f"网络响应 #{getattr(request, 'network_log_id', '?')}：HTTP {response.code} | 耗时={seconds:.2f}s | 字节数={length} | Content-Range={byte_range}")
        return response

    http_response = https_response


class NetworkLogOpener:
    def __init__(self, opener, report):
        self.opener, self.report = opener, report

    def __getattr__(self, name):
        return getattr(self.opener, name)

    def open(self, request, *args, **kwargs):
        try:
            return self.opener.open(request, *args, **kwargs)
        except (OSError, http.client.HTTPException) as error:
            url = request.full_url if isinstance(request, urllib.request.Request) else request
            if isinstance(error, urllib.error.HTTPError):
                url = error.geturl()
            detail = f"HTTP {error.code} {error.reason}" if isinstance(error, urllib.error.HTTPError) else type(getattr(error, "reason", error)).__name__
            self.report(None, f"网络连接失败：{safe_url(url)} | 原因={detail}")
            raise
