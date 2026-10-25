"""Link previews (links-yt). Server-side fetch with SSRF guards. Only used by signed-in accounts."""
import html as _html
import http.client
import ipaddress
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

import bcconfig

TIMEOUT = 5
PAGE_CAP = 1024 * 1024
IMAGE_CAP = 3 * 1024 * 1024
MAX_REDIRECTS = 3
CACHE_SECONDS = 24 * 3600
UA = "Mozilla/5.0 (compatible; BranchChatPreview/1.0; +%s)" % bcconfig.PUBLIC_URL

_lock = threading.Lock()
_pages = {}   # url -> (stamp, result or None)
_images = {}  # token -> (stamp, url)
_hits = {}    # user -> [stamps]


def allowed(user_key, limit=40):
    now = time.time()
    with _lock:
        hits = [t for t in _hits.get(user_key, []) if now - t < 60]
        if len(hits) >= limit:
            _hits[user_key] = hits
            return False
        hits.append(now)
        _hits[user_key] = hits
        if len(_hits) > 2000:
            _hits.clear()
    return True


def _public_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local
                                 or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _resolve(host, port):
    """Every address must be public. Returns one to connect to, so DNS cannot change after the check."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addrs = [info[4][0] for info in infos]
    if not addrs or not all(_public_ip(a) for a in addrs):
        raise ValueError("blocked address")
    return addrs[0]


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, port, ip, tls):
        super().__init__(host, port, timeout=TIMEOUT)
        self._ip, self._tls = ip, tls

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), TIMEOUT)
        if self._tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


def _check_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("bad url")
    if parts.username or parts.password:
        raise ValueError("bad url")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".lan") or "." not in host:
        raise ValueError("blocked host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise ValueError("bad port")
    if port not in (80, 443, 8080, 8443):
        raise ValueError("blocked port")
    return parts, host, port


def fetch(url, kinds, cap):
    """GET with redirects re-checked each hop. Returns (final_url, content_type, bytes)."""
    deadline = time.time() + TIMEOUT * 2
    for _hop in range(MAX_REDIRECTS + 1):
        parts, host, port = _check_url(url)
        ip = _resolve(host, port)
        conn = _Pinned(host, port, ip, parts.scheme == "https")
        try:
            path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
            conn.request("GET", path, headers={"Host": parts.netloc.split("@")[-1], "User-Agent": UA,
                         "Accept": "text/html,image/*;q=0.9,*/*;q=0.1", "Accept-Language": "en", "Accept-Encoding": "identity"})
            res = conn.getresponse()
            if res.status in (301, 302, 303, 307, 308):
                loc = res.getheader("Location")
                if not loc:
                    raise ValueError("bad redirect")
                url = urljoin(url, loc)
                continue
            if res.status != 200:
                raise ValueError("status %d" % res.status)
            ctype = (res.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if not any(ctype == k or (k.endswith("/") and ctype.startswith(k)) for k in kinds):
                raise ValueError("type")
            length = res.getheader("Content-Length")
            if length and length.isdigit() and int(length) > cap:
                raise ValueError("too big")
            chunks, size = [], 0
            while size <= cap:
                if time.time() > deadline:
                    raise ValueError("slow")
                piece = res.read(65536)
                if not piece:
                    break
                chunks.append(piece)
                size += len(piece)
            body = b"".join(chunks)
            if size > cap:
                if kinds == ("text/html",):
                    body = body[:cap]
                else:
                    raise ValueError("too big")
            return url, ctype, body
        finally:
            conn.close()
    raise ValueError("too many redirects")


_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')


def _clean(text, limit):
    text = re.sub(r"\s+", " ", _html.unescape(text or "")).strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def parse(page_url, body):
    text = body.decode("utf-8", "replace")
    head = text[: text.lower().find("</head>")] if "</head>" in text.lower() else text[:300000]
    meta = {}
    for tag in _META.findall(head):
        attrs = {}
        for m in _ATTR.finditer(tag):
            attrs[m.group(1).lower()] = m.group(3) if m.group(3) is not None else (m.group(4) if m.group(4) is not None else m.group(5))
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs and key not in meta:
            meta[key] = attrs["content"]
    title = meta.get("og:title") or meta.get("twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        title = m.group(1) if m else ""
    desc = meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or ""
    site = meta.get("og:site_name") or ""
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image") or ""
    out = {"title": _clean(title, 200), "description": _clean(desc, 300),
           "site": _clean(site, 80) or (urlsplit(page_url).hostname or "").removeprefix("www."), "url": page_url}
    if image:
        image = urljoin(page_url, _html.unescape(image).strip())
        if urlsplit(image).scheme in ("http", "https"):
            token = secrets.token_urlsafe(18)
            with _lock:
                _images[token] = (time.time(), image)
                if len(_images) > 3000:
                    for old in sorted(_images, key=lambda k: _images[k][0])[:1000]:
                        _images.pop(old, None)
            out["image"] = "/api/unfurl/image?t=" + token
    if not out["title"] and not out["description"]:
        return None
    return out


def unfurl(url):
    """Preview dict or None. Cached about a day, failures too (shorter)."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    now = time.time()
    with _lock:
        hit = _pages.get(url)
        if hit and now - hit[0] < (CACHE_SECONDS if hit[1] else 1800):
            return hit[1]
    try:
        final, _ctype, body = fetch(url, ("text/html",), PAGE_CAP)
        result = parse(final, body)
    except Exception:
        result = None
    with _lock:
        _pages[url] = (now, result)
        if len(_pages) > 2000:
            for old in sorted(_pages, key=lambda k: _pages[k][0])[:500]:
                _pages.pop(old, None)
    return result


def image(token):
    """(content_type, bytes) for an image a preview pointed at, or None."""
    with _lock:
        hit = _images.get(token or "")
    if not hit or time.time() - hit[0] > CACHE_SECONDS:
        return None
    try:
        _final, ctype, body = fetch(hit[1], ("image/",), IMAGE_CAP)
    except Exception:
        return None
    if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"):
        return None
    return ctype, body
