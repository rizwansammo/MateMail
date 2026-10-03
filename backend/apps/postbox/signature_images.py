"""
Remote images in a mailbox's own HTML signature, fetched by MateMail so the
native PostBox app can show them in its signature preview.

WHY THIS EXISTS
    `mime.sanitize_signature` keeps remote <img> sources on purpose: the owner
    chose them (their own logo) and recipients' mail clients load them. The
    native app never loads a remote image itself - no third-party request
    leaves the device - so without this its preview could only ever show a
    placeholder where the logo goes.

NOT A URL FETCHER
    The caller names a signature it owns and an index. The URL comes from
    that signature's STORED, already-sanitised HTML (`https_sources`); nothing
    the client sends is ever fetched. It serves signatures only - never
    message bodies, which keep their own remote-image policy.

THE FETCH (`fetch`)
    - https only, port 443 only, no userinfo; at most MAX_REDIRECTS redirects,
      each hop checked again from the top.
    - The hostname is resolved once per hop and EVERY address must be
      globally routable (no loopback, private, link-local, CGNAT, multicast,
      reserved or unspecified address, IPv4-mapped IPv6 included). The
      connection then goes to that checked address, with TLS verified
      against the hostname - so DNS cannot answer differently between the
      check and the connection (rebinding).
    - Connect and read timeouts, and an overall deadline.
    - At most MAX_BYTES: Content-Length is checked first, then the body is
      counted as it arrives.
    - The declared Content-Type must be PNG, JPEG, GIF or WebP AND the bytes
      must sniff as one of them; the sniffed type is what gets served. SVG
      (a document that can carry script) is never accepted.

Every failure is a `SignatureImageError` with a short code for logs; the URL
is never logged or returned.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3
from django.core.cache import cache

MAX_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
DEADLINE_SECONDS = 10.0

#: How long a fetched image (or a failure) is reused for one signature
#: version. A changed signature has a new `updated_at`, so a new key.
CACHE_SECONDS = 3600
FAILURE_CACHE_SECONDS = 300

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_REDIRECTS = {301, 302, 303, 307, 308}


class SignatureImageError(Exception):
    """The image can't be served. `code` is safe to log; the URL is not."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchedImage:
    content_type: str
    data: bytes


# ── which images ────────────────────────────────────────────────────────────


class _ImageSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        src = (dict(attrs).get("src") or "").strip()
        if src[:8].lower() == "https://":
            self.sources.append(src)

    handle_startendtag = handle_starttag


def https_sources(html: str) -> list[str]:
    """
    The https <img> sources of a signature, in document order.

    The index into this list is the whole contract with the client, which
    numbers the same images the same way: every <img> whose src starts with
    `https://`, in order, whether or not it ends up drawn.
    """
    parser = _ImageSources()
    parser.feed(html or "")
    parser.close()
    return parser.sources


# ── where it may come from ──────────────────────────────────────────────────


def _checked(address: str) -> str:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        raise SignatureImageError("blocked_address")
    return str(ip)


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SignatureImageError("dns") from exc
    return [str(info[4][0]) for info in infos]


def public_addresses(host: str, port: int = 443, resolve=_resolve) -> list[str]:
    """Every address [host] resolves to, all public - or an error."""
    addresses = resolve(host, port)
    if not addresses:
        raise SignatureImageError("dns")
    # All of them, not the first: a mixed answer is how rebinding starts.
    return list(dict.fromkeys(_checked(a) for a in addresses))


def _target(url: str):
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise SignatureImageError("scheme")
    if parts.username or parts.password:
        raise SignatureImageError("userinfo")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise SignatureImageError("port") from exc
    if port != 443:
        raise SignatureImageError("port")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise SignatureImageError("host")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return host, path


# ── the request ─────────────────────────────────────────────────────────────


def _open(address: str, host: str, path: str):
    """GET [path] from [address], TLS verified for [host]; streamed."""
    pool = urllib3.HTTPSConnectionPool(
        address,
        443,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        server_hostname=host,
        assert_hostname=host,
        timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT),
        retries=False,
        maxsize=1,
    )
    return pool.urlopen(
        "GET",
        path,
        headers={
            "Host": host,
            "Accept": "image/png,image/jpeg,image/gif,image/webp",
            "User-Agent": "MateMail-PostBox-SignatureImage/1.0",
        },
        redirect=False,
        retries=False,
        preload_content=False,
    )


def _sniff(payload: bytes):
    # Late: views_settings imports this module.
    from .views_settings import _sniff_image

    return _sniff_image(payload)


def fetch(url: str, *, resolve=_resolve, open_connection=_open, clock=time.monotonic):
    """The image at [url], under every rule in the module docstring."""
    deadline = clock() + DEADLINE_SECONDS
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        host, path = _target(current)
        addresses = public_addresses(host, resolve=resolve)
        response = None
        for address in addresses:
            try:
                response = open_connection(address, host, path)
                break
            except (urllib3.exceptions.HTTPError, OSError):
                continue
        if response is None:
            raise SignatureImageError("connect")
        try:
            status = response.status
            if status in _REDIRECTS:
                location = response.headers.get("Location")
                if not location:
                    raise SignatureImageError("status")
                current = urljoin(current, location)
                continue
            if status != 200:
                raise SignatureImageError("status")
            declared = (response.headers.get("Content-Type") or "").split(";")[0]
            if declared.strip().lower() not in ALLOWED_TYPES:
                raise SignatureImageError("content_type")
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise SignatureImageError("too_large")
            body = bytearray()
            try:
                for chunk in response.stream(16 * 1024):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise SignatureImageError("too_large")
                    if clock() > deadline:
                        raise SignatureImageError("timeout")
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                raise SignatureImageError("read") from exc
            sniffed = _sniff(bytes(body))
            if sniffed is None:
                raise SignatureImageError("not_image")
            return FetchedImage(content_type=sniffed[0], data=bytes(body))
        finally:
            close = getattr(response, "release_conn", None) or getattr(
                response, "close", None
            )
            if close:
                close()
    raise SignatureImageError("redirects")


def cached_fetch(signature, index: int, url: str, *, fetcher=None) -> FetchedImage:
    """
    [fetch], reused for this signature version: a preview opened twice asks
    the logo's host once, and a failing host isn't asked again for a while.
    """
    version = signature.updated_at.timestamp() if signature.updated_at else 0
    key = f"postbox:signature-image:{signature.pk}:{version}:{index}"
    hit = cache.get(key)
    if isinstance(hit, dict):
        if "error" in hit:
            raise SignatureImageError(hit["error"])
        return FetchedImage(content_type=hit["type"], data=hit["data"])
    try:
        image = (fetcher or fetch)(url)
    except SignatureImageError as exc:
        cache.set(key, {"error": exc.code}, FAILURE_CACHE_SECONDS)
        raise
    cache.set(key, {"type": image.content_type, "data": image.data}, CACHE_SECONDS)
    return image
