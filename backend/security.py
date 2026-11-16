"""SSRF protection, robots.txt handling and the single safe HTTP fetch path.

Every outbound request made by the CLI tools goes through ``safe_get`` so that
URL validation, DNS checks, redirect re-validation, size limits and timeouts
cannot be bypassed.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = "WebPilotAI/1.0 (+hackathon demo crawler; honours robots.txt)"
ROBOTS_TOKEN = "WebPilotAI"
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT = (5, 12)  # connect, read
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 2048
ALLOWED_PORTS = {80, 443, 8080, 8443}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp")
HTML_TYPES = ("text/html", "application/xhtml+xml")


class UrlRejected(ValueError):
    """The URL failed validation (syntax, scheme, SSRF checks)."""


class RobotsDisallowed(Exception):
    """robots.txt forbids fetching the URL."""


class FetchError(Exception):
    def __init__(self, message: str, transient: bool = False, status: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.status = status


def allow_private() -> bool:
    """TEST ONLY: lets the automated tests crawl a local fixture server."""
    return os.environ.get("WEBPILOT_ALLOW_PRIVATE_TEST") == "1"


def is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    return ip.is_global


def validate_url(url: str) -> str:
    """Validate a URL and return its normalised form, or raise UrlRejected."""
    if not isinstance(url, str) or not url.strip():
        raise UrlRejected("URL is empty")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise UrlRejected("URL is too long")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UrlRejected("URL contains whitespace or control characters")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise UrlRejected("URL is malformed")
    if parsed.scheme not in ("http", "https"):
        raise UrlRejected("Only http:// and https:// URLs are allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRejected("URL has no hostname")
    if parsed.username or parsed.password:
        raise UrlRejected("URLs with embedded credentials are not allowed")
    if not allow_private():
        if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
            raise UrlRejected("Local and internal hostnames are not allowed")
        if port is not None and port not in ALLOWED_PORTS:
            raise UrlRejected(f"Port {port} is not allowed")
    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UrlRejected(f"Could not resolve hostname '{host}'")
    addrs = {i[4][0] for i in infos}
    if not addrs:
        raise UrlRejected(f"Could not resolve hostname '{host}'")
    if not allow_private():
        for addr in addrs:
            if not is_public_ip(addr):
                raise UrlRejected(
                    f"Host '{host}' resolves to a private or reserved address ({addr}); blocked to prevent SSRF")
    return parsed._replace(fragment="").geturl()


@dataclass
class FetchResult:
    final_url: str
    status: int
    content_type: str
    content: bytes
    truncated: bool
    redirects: int


def safe_get(url: str, accept: tuple[str, ...] | None = HTML_TYPES,
             max_bytes: int = MAX_RESPONSE_BYTES) -> FetchResult:
    """GET with manual redirect handling; every hop is re-validated."""
    current = validate_url(url)
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5,*/*;q=0.1",
    })
    hops = 0
    while True:
        try:
            resp = session.get(current, timeout=REQUEST_TIMEOUT, allow_redirects=False, stream=True)
        except requests.exceptions.SSLError:
            raise FetchError("TLS/SSL certificate error", transient=False)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            raise FetchError(f"Connection problem: {type(exc).__name__}", transient=True)
        except requests.exceptions.RequestException as exc:
            raise FetchError(f"Request failed: {type(exc).__name__}", transient=False)
        try:
            code = resp.status_code
            if code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    raise FetchError("Redirect without Location header")
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise FetchError("Too many redirects")
                current = validate_url(urljoin(current, loc))  # re-validate destination
                continue
            if code >= 500 or code == 429:
                raise FetchError(f"HTTP {code}", transient=True, status=code)
            if code >= 400:
                raise FetchError(f"HTTP {code}", transient=False, status=code)
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if accept and not any(ctype.startswith(a) for a in accept):
                raise FetchError(f"Unsupported content type '{ctype or 'unknown'}'")
            declared = resp.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > max_bytes * 5:
                raise FetchError("Response is too large")
            buf, size, truncated = [], 0, False
            try:
                for chunk in resp.iter_content(chunk_size=16384):
                    size += len(chunk)
                    if size > max_bytes:
                        keep = len(chunk) - (size - max_bytes)
                        buf.append(chunk[:max(keep, 0)])
                        truncated = True
                        break
                    buf.append(chunk)
            except requests.exceptions.RequestException:
                raise FetchError("Connection lost while reading response", transient=True)
            return FetchResult(current, code, ctype, b"".join(buf), truncated, hops)
        finally:
            resp.close()


class RobotsChecker:
    """Fetches and caches robots.txt per origin using the safe fetch path."""

    def __init__(self) -> None:
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}

    def _load(self, origin: str, url: str):
        if origin in self._cache:
            return self._cache[origin]
        parser = robotparser.RobotFileParser()
        try:
            res = safe_get(origin + "/robots.txt", accept=None, max_bytes=500_000)
            parser.parse(res.content.decode("utf-8", errors="replace").splitlines())
        except FetchError as exc:
            if exc.status and 400 <= exc.status < 500 and exc.status != 429:
                parser = None  # no robots.txt -> everything allowed
            else:
                raise  # 5xx / network trouble: be conservative, do not crawl
        self._cache[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        parser = self._load(origin, url)
        return True if parser is None else parser.can_fetch(ROBOTS_TOKEN, url)
