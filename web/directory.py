"""Branch Chat directory: server identity, invite links through the home server, signed directory leases.

Two roles, both off by default:

* Reporting server (BC_DIRECTORY_REPORT=1): every BC_DIRECTORY_PING_SECONDS (default 300 s, +-30 s jitter) it POSTs
  a signed ping to BC_HOME_URL (default https://branchchat.app)/api/directory/lease:
      {"v": 2, "login_url": ..., "ts": <unix seconds>, "online": true, "key": <Ed25519 public key>, "sig": ...}
  sig = Ed25519(private identity key, "bc-lease-v2\n<login_url>\n<ts>\n<online 1|0>"). The private key is made
  on first use in STATE_DIR/server-identity.json (mode 600) and never leaves the server. Nothing else is sent:
  no name, no icon, no user counts, no messages.
  With reporting on, invite links go through the home: <home>/join?s=<host>&n=<name>&k=<key fingerprint>#t=<token>.
  With reporting off (the default), invite links go straight to <this server>/welcome#t=<token>.

* Home / directory server (BC_HOME_MODE=1) stores per server ONLY: the public key, the login URL, the last ping
  time (and the last signed timestamp, to refuse replays), plus admin flags. It refuses stale timestamps
  (more than 120 s off), replays (a timestamp not newer than the last one), bad signatures and revoked keys.
  Ownership: on first contact (trust on first use) the home fetches the login URL host's /.well-known/branchchat
  and requires it to show the same public key. Hosts the home cannot reach (a home-network-only server, or a
  private address, which the home refuses to fetch) stay pending until an admin vouches for the key.
  Name and icon are never stored: the home reads them from the server's /.well-known/branchchat and
  /directory-icon on the fly and keeps them in memory for 10 minutes (16 KB / 64 KB, PNG/JPEG/WebP only,
  cleaned). When the home can't reach a server, the directory page reads them from the visitor's browser.
  A listing is shown while its last ping is younger than BC_DIRECTORY_TTL (default 900 s = three missed pings);
  records with no ping for 30 days are deleted. Revoking a key (manage.py or the admin API) delists at once.
"""
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import random
import re
import secrets
import socket
import ssl
import threading
import time
import unicodedata
from urllib.parse import quote, urlsplit

import bcconfig

STATE_DIR = bcconfig.STATE_DIR
APP_DIR = bcconfig.APP_DIR


# True when a config switch is on (1, true, yes or on).
def _flag(name, default="0"):
    return (bcconfig.get(name, default).strip().lower() in ("1", "true", "yes", "on"))


REPORT = _flag("BC_DIRECTORY_REPORT")
HOME_MODE = _flag("BC_HOME_MODE")
HOME_URL = (bcconfig.get("BC_HOME_URL", "https://branchchat.app").strip() or "https://branchchat.app").rstrip("/")
_hu = urlsplit(HOME_URL)
HOME_ORIGIN = "%s://%s" % (_hu.scheme, _hu.netloc)
PING_SECONDS = max(60, bcconfig.get_int("BC_DIRECTORY_PING_SECONDS", 300))
TTL = max(30, bcconfig.get_int("BC_DIRECTORY_TTL", 900))
REVERIFY_SECONDS = 6 * 3600
FORGET_SECONDS = 30 * 86400
# Test switches for the home role. Never set these on a public home.
ALLOW_HTTP = _flag("BC_DIRECTORY_ALLOW_HTTP")
ALLOW_PRIVATE = _flag("BC_DIRECTORY_ALLOW_PRIVATE")

NAME_MAX = 48
ICON_MAX = 64 * 1024
LISTING_MAX = 5000
PENDING_MAX = 200
ICON_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
UA = "BranchChat-Lease/2 (+https://branchchat.app/directory)"
SKEW = 120
INFO_CACHE_SECONDS = 600

IDENTITY_PATH = os.path.join(STATE_DIR, "server-identity.json")   # every server: Ed25519 identity key (mode 600)
PAUSE_PATH = os.path.join(STATE_DIR, "directory-pause-until")     # reporting server: manage.py directory-pause
STATUS_PATH = os.path.join(STATE_DIR, "directory-status.json")    # reporting server: last ping result
STORE_PATH = os.path.join(STATE_DIR, "directory.json")            # home: listings

_lock = threading.Lock()


# ---------------------------------------------------------------- small helpers
def b64u(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# Write a file atomically (temp file, fsync, rename), private to the server user.
def _atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# Read a JSON file; the fallback when it is missing, broken or the wrong type.
def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, type(fallback)) else fallback
    except (OSError, ValueError):
        return fallback


def clean_name(value):
    """One line of plain text: control and format characters removed, spaces collapsed, at most NAME_MAX."""
    if not isinstance(value, str):
        return ""
    out = []
    for ch in unicodedata.normalize("NFC", value):
        cat = unicodedata.category(ch)
        if cat[0] == "C" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif ch in "<>":
            continue
        else:
            out.append(ch)
    text = " ".join("".join(out).split())
    return text[:NAME_MAX].strip()


def icon_kind(raw):
    """png / jpg / webp by magic bytes, else ''. SVG and everything else is refused (scripts)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 12 or len(raw) > ICON_MAX:
        return ""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return ""


def norm_origin(url, allow_http=False):
    """'https://host[:port]' for a login URL, or '' when it is not acceptable."""
    if not isinstance(url, str) or len(url) > 300 or any(c in url for c in " \r\n\t\\\"'<>"):
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ""
    scheme = (parts.scheme or "").lower()
    if scheme != "https" and not (allow_http and scheme == "http"):
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        return ""
    if not re.match(r"^[a-z0-9.-]{1,253}$", host) and not re.match(r"^[0-9a-f:]+$", host):
        return ""
    if port is not None and not (0 < port < 65536):
        return ""
    default = 443 if scheme == "https" else 80
    netloc = host if port in (None, default) else "%s:%d" % (host, port)
    if ":" in host and not host.startswith("["):
        netloc = "[%s]" % host if port in (None, default) else "[%s]:%d" % (host, port)
    return "%s://%s" % (scheme, netloc)


def host_key(origin):
    """Listing key: the origin without the scheme, e.g. 'chat.example.com' or 'chat.example.org:8443'."""
    return origin.split("://", 1)[1]



# ---------------------------------------------------------------- this server's identity (every server)
def _ed():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519


def _identity():
    """(private key object, raw public key bytes). Made on first use; the private key never leaves STATE_DIR."""
    from cryptography.hazmat.primitives import serialization as ser
    ed = _ed()
    with _lock:
        data = _read_json(IDENTITY_PATH, {})
        raw = None
        try:
            raw = base64.b64decode(data.get("ed25519_private", ""), validate=True)
        except (ValueError, TypeError):
            raw = None
        if not raw or len(raw) != 32:
            priv = ed.Ed25519PrivateKey.generate()
            raw = priv.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
            _atomic(IDENTITY_PATH, json.dumps({"ed25519_private": base64.b64encode(raw).decode("ascii"),
                                               "created": int(time.time())}))
        priv = ed.Ed25519PrivateKey.from_private_bytes(raw)
        pub = priv.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
        return priv, pub


# This server's public identity key (Ed25519), URL-safe base64.
def public_key_b64():
    return b64u(_identity()[1])


# Short fingerprint of an identity key, as shown in the directory and admin tools.
def fingerprint_of(pub_b64):
    try:
        raw = base64.urlsafe_b64decode(pub_b64 + "=" * (-len(pub_b64) % 4))
    except (ValueError, TypeError):
        return ""
    return b64u(hashlib.sha256(b"bc-identity-v1:" + raw).digest())[:32]


# This server's own identity fingerprint.
def fingerprint():
    return fingerprint_of(public_key_b64())


# The exact bytes a directory lease ping signs: version, login address, time and online flag.
def lease_message(login_url, ts, online):
    return ("bc-lease-v2\n%s\n%d\n%d" % (login_url, int(ts), 1 if online else 0)).encode("utf-8")


# Sign a lease ping with this server's identity key; returns (signature, public key).
def sign_lease(login_url, ts, online):
    priv, pub = _identity()
    return b64u(priv.sign(lease_message(login_url, ts, online))), b64u(pub)


# Check a lease ping's signature against the key it carries. False for anything malformed.
def verify_lease(pub_b64, sig_b64, login_url, ts, online):
    try:
        pub = base64.urlsafe_b64decode(pub_b64 + "=" * (-len(pub_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        if len(pub) != 32 or len(sig) != 64:
            return False
        _ed().Ed25519PublicKey.from_public_bytes(pub).verify(sig, lease_message(login_url, ts, online))
        return True
    except Exception:  # InvalidSignature, ValueError
        return False


# The name this server shows for itself: BC_ORG_NAME, else the instance name, else "Branch Chat".
def org_name():
    return clean_name(bcconfig.get("BC_ORG_NAME", "") or bcconfig.INSTANCE_NAME or "BranchChat") or "BranchChat"


def org_icon():
    """(bytes, kind) for BC_ORG_ICON (a png/jpg/webp file up to 64 KB), else the stock favicon.png."""
    path = bcconfig.get("BC_ORG_ICON", "").strip()
    for candidate in ([path] if path else []) + [os.path.join(APP_DIR, "favicon.png")]:
        try:
            with open(candidate, "rb") as fh:
                raw = fh.read(ICON_MAX + 1)
        except OSError:
            continue
        kind = icon_kind(raw)
        if kind:
            return raw, kind
    return b"", ""


# 0.23.3-beta.12: optional organisation type, published in /.well-known/branchchat (the home shows e.g. a "Family" tag).
# Not part of the signed ping: homes refuse unknown ping fields, so a new field there would delist servers on older homes.
ORG_TYPES = ("family", "church", "school", "club", "business", "community")


# What kind of group this server is (BC_ORG_TYPE, e.g. family), only from the allowed list.
def org_type():
    value = bcconfig.get("BC_ORG_TYPE", "").strip().lower()
    return value if value in ORG_TYPES else ""


def org_configured():
    """True when the admin set an organisation name or icon (BC_ORG_NAME / BC_INSTANCE_NAME / BC_ORG_ICON)."""
    return bool(bcconfig.get("BC_ORG_NAME", "").strip() or bcconfig.INSTANCE_NAME or bcconfig.get("BC_ORG_ICON", "").strip())


def configured_org_icon():
    """(bytes, kind) of BC_ORG_ICON only (no stock-favicon fallback), for showing the org's own picture in pages."""
    path = bcconfig.get("BC_ORG_ICON", "").strip()
    if not path:
        return b"", ""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(ICON_MAX + 1)
    except OSError:
        return b"", ""
    kind = icon_kind(raw)
    return (raw, kind) if kind else (b"", "")


# Short hash of a picture, used as a cache-buster in its URL.
def icon_hash(raw):
    return hashlib.sha256(raw).hexdigest()[:16] if raw else ""


def well_known(public_url, version):
    """Public server-info document (GET /.well-known/branchchat). Safe to cache and to read cross-site."""
    raw, _kind = org_icon()
    return {
        "branchchat": 2,
        "name": org_name(),
        "login_url": public_url + "/",
        "version": version,
        "identity_key": public_key_b64(),
        "key_fp": fingerprint(),
        "icon": "/directory-icon" if raw else "",
        "icon_hash": icon_hash(raw),
        "directory": HOME_URL if REPORT else "",
        "type": org_type(),
        "invites": {"check": "/api/invite/check", "welcome": "/welcome"},
    }


def invite_link(public_url, token):
    """26.10.10: always through the home's landing page (<home>/join?s=...#t=...), reporting on or off, so the
    BranchChat Android app (which verifies only the home's links) can catch every server's invites. The token stays
    after "#", so the home never sees it. Direct links only for http test installs, the home itself, or
    BC_INVITE_DIRECT=1."""
    if bcconfig.get("BC_INVITE_DIRECT", "") == "1" or (public_url.startswith("http://") and not REPORT) \
            or (norm_origin(public_url, True) or "").rstrip("/") == HOME_URL:
        return public_url + "/welcome#t=" + token
    server = host_key(norm_origin(public_url, True) or public_url)
    if public_url.startswith("http://"):
        server = public_url  # test installs only; the home must allow http
    name = org_name()[:40]
    return "%s/join?s=%s&n=%s&k=%s#t=%s" % (HOME_URL, quote(server, safe=":[]"), quote(name, safe=""), fingerprint()[:12], token)


# ---------------------------------------------------------------- reporting server: the signed pinger
def paused_until():
    try:
        with open(PAUSE_PATH) as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


# POST JSON to a URL (HTTPS verified normally) and return (status, reply).
def _post_json(url, obj, timeout=15):
    body = json.dumps(obj).encode("utf-8")
    parts = urlsplit(url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    kw = {"timeout": timeout}
    if parts.scheme == "https":
        kw["context"] = ssl.create_default_context()
    conn = conn_cls(parts.hostname, parts.port, **kw)
    try:
        conn.request("POST", parts.path or "/", body=body, headers={
            "Content-Type": "application/json", "User-Agent": UA, "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(65536)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            data = {}
        return resp.status, data if isinstance(data, dict) else {}
    finally:
        conn.close()


# Build a signed lease ping saying this server is (or is not) online at its login address.
def make_ping(public_url, online=True, ts=None):
    login_url = public_url + "/"
    ts = int(ts if ts is not None else time.time())
    sig, pub = sign_lease(login_url, ts, online)
    return {"v": 2, "login_url": login_url, "ts": ts, "online": bool(online), "key": pub, "sig": sig}


# Send one lease ping to the directory home.
def ping_once(public_url, online=True):
    return _post_json(HOME_URL + "/api/directory/lease", make_ping(public_url, online))


# Record the last directory result in the status file (for manage.py directory-status) and the log.
def _note(text, status, reply):
    row = {"at": int(time.time()), "status": status, "state": reply.get("state", ""), "message": text}
    try:
        _atomic(STATUS_PATH, json.dumps(row))
    except OSError:
        pass
    print("directory: %s" % text, flush=True)


# Background loop: ping the directory home every few minutes, honouring a pause set by manage.py.
def _pinger(public_url):
    time.sleep(8)
    last_state = None
    while True:
        wait = PING_SECONDS + random.randint(-30, 30)
        if paused_until() > time.time():
            if last_state != "paused":
                _note("lease pings paused by manage.py until %s" % time.strftime("%H:%M:%S", time.localtime(paused_until())), 0, {})
                last_state = "paused"
            time.sleep(10)
            continue
        try:
            status, reply = ping_once(public_url)
            state = reply.get("state") or ("http %d" % status)
            if state != last_state:
                _note("lease ping to %s: %s%s" % (HOME_URL, state, (" (" + " ".join(str(reply.get("error", ""))[:200].split()) + ")") if reply.get("error") else ""), status, reply)
                last_state = state
            else:
                _note_quiet(status, reply)
            if status == 429:
                wait = max(wait, int(reply.get("retry", 60) or 60))
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            if last_state != "unreachable":
                _note("lease ping to %s failed: %s" % (HOME_URL, exc.__class__.__name__), 0, {})
                last_state = "unreachable"
            wait = min(wait, 120)
        slept = 0
        while slept < wait and paused_until() <= time.time():
            time.sleep(10)
            slept += 10


# Update the status file's time and state without changing its message (for routine successful pings).
def _note_quiet(status, reply):
    try:
        row = _read_json(STATUS_PATH, {})
        row.update({"at": int(time.time()), "status": status, "state": reply.get("state", "")})
        _atomic(STATUS_PATH, json.dumps(row))
    except OSError:
        pass


# Start the lease pinger thread when this server reports to a directory (BC_DIRECTORY_REPORT).
def start_reporter(public_url):
    if not REPORT:
        return
    _identity()
    threading.Thread(target=_pinger, args=(public_url,), name="directory-lease", daemon=True).start()
    print("directory: reporting on; signed lease pings to %s every ~%d s" % (HOME_URL, PING_SECONDS), flush=True)


# ---------------------------------------------------------------- home: store (public key, login URL, last ping)
def _load():
    data = _read_json(STORE_PATH, {})
    data.setdefault("listings", {})
    data.setdefault("revoked", [])
    data.setdefault("pinned", {})
    return data


# Save the directory home's listings store.
def _save(data):
    _atomic(STORE_PATH, json.dumps(data, sort_keys=True))


# Fingerprints of identity keys the directory admin revoked.
def _revoked_fps(store):
    return {r.get("fp") for r in store.get("revoked", [])}


# Should this listing show in the public directory: verified, online, not hidden/banned/revoked, recent ping.
def _listed(row, now=None, revoked=()):
    now = now or time.time()
    return bool(row.get("verified") and row.get("online") and not row.get("hidden") and not row.get("banned")
                and fingerprint_of(row.get("key", "")) not in revoked
                and now - int(row.get("last_ping") or 0) <= TTL)


# ---- name + icon, read on the fly from the server and kept in memory only
_info = {}  # host -> {"at", "ok", "name", "icon": bytes, "icon_type"}
_info_lock = threading.Lock()


# Fetch a small file from a listed server, connecting only to the public address checked earlier (no tricks via DNS).
def _fetch_bytes(origin, path, limit, accept):
    parts = urlsplit(origin)
    host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    ip = _public_ip(host, port)
    if parts.scheme == "https":
        conn = _PinnedHTTPS(host, port, ip, timeout=5, context=ssl.create_default_context())
    else:
        conn = _PinnedHTTP(host, port, ip, timeout=5)
    try:
        conn.request("GET", path, headers={"User-Agent": UA, "Accept": accept})
        resp = conn.getresponse()
        if resp.status != 200:
            raise ValueError("HTTP %d" % resp.status)
        raw = resp.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("too large")
        return raw
    finally:
        conn.close()


def fetch_well_known(origin):
    """GET <origin>/.well-known/branchchat: 5 s, 16 KB, no redirects, connects to the address that was checked."""
    doc = json.loads(_fetch_bytes(origin, "/.well-known/branchchat", 16384, "application/json").decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    return doc


# Organisation types a server may publish in /.well-known/branchchat ("type"); anything else is ignored (ORG_TYPES above).
def clean_type(value):
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in ORG_TYPES else ""


def server_info(row, force=False):
    """Cleaned name + icon for a listing, cached in memory for INFO_CACHE_SECONDS. ok=False when unreachable."""
    hk = row["host"]
    now = time.time()
    with _info_lock:
        hit = _info.get(hk)
        if hit and not force and now - hit["at"] < (INFO_CACHE_SECONDS if hit["ok"] else 120):
            return hit
    out = {"at": now, "ok": False, "name": "", "icon": b"", "icon_type": "", "type": ""}
    try:
        doc = fetch_well_known(row["origin"])
        if doc.get("identity_key") == row.get("key"):
            out["ok"] = True
            out["name"] = clean_name(doc.get("name") or "")
            out["type"] = clean_type(doc.get("type"))
            ipath = doc.get("icon") or ""
            if isinstance(ipath, str) and re.match(r"^/[A-Za-z0-9/_-]{1,64}$", ipath):
                try:
                    raw = _fetch_bytes(row["origin"], ipath, ICON_MAX, "image/png,image/jpeg,image/webp")
                    kind = icon_kind(raw)
                    if kind:
                        out["icon"], out["icon_type"] = raw, kind
                except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
                    pass
    except (OSError, ValueError, PermissionError, http.client.HTTPException, ssl.SSLError):
        pass
    if not out["ok"]:
        out = _vouch_info(row, out)
    with _info_lock:
        _info[hk] = out
        if len(_info) > LISTING_MAX:
            for k in sorted(_info, key=lambda k: _info[k]["at"])[:500]:
                _info.pop(k, None)
    return out


def _vouch_info(row, out):
    """A vouched listing the home cannot reach (e.g. a LAN-only server) shows the name and icon the admin gave
    with directory-vouch --name/--icon. Nothing is fetched; the icon file lives in PINNED_DIR."""
    if row.get("verified_by") != "vouch" or not row.get("verified") or not row.get("admin_name"):
        return out
    raw, kind = _pinned_icon({"icon_file": row.get("admin_icon_file", "")})
    return {"at": out["at"], "ok": True, "name": clean_name(row["admin_name"]), "icon": raw or b"",
            "icon_type": kind if raw else "", "admin": True}


def _store_icon(hk, icon_src, prefix=""):
    """Copy an admin-supplied icon into PINNED_DIR. Returns (file name, error)."""
    try:
        with open(icon_src, "rb") as fh:
            raw = fh.read(PINNED_ICON_MAX + 1)
    except OSError as exc:
        return "", "Can't read the icon: %s" % exc
    ikind = _image_kind(raw, PINNED_ICON_MAX)
    if not ikind:
        return "", "The icon must be a PNG, JPEG or WebP up to %d KB." % (PINNED_ICON_MAX // 1024)
    os.makedirs(PINNED_DIR, exist_ok=True)
    name = prefix + re.sub(r"[^A-Za-z0-9.-]", "_", hk) + "." + ikind
    dest = os.path.join(PINNED_DIR, name)
    if os.path.abspath(icon_src) != os.path.abspath(dest):
        tmp = dest + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, dest)
    return name, ""


# What the public directory shows for one listing (name and icon come from the server's own info).
def public_row(row, info):
    return {
        "id": row["id"],
        "name": info["name"] if info["ok"] else "",
        "server": row["host"],
        "login_url": row["origin"] + "/",
        "icon": ("/directory/icon/%s?h=%s" % (row["id"], icon_hash(info["icon"]))) if info["icon"] else "",
        "info_from_server": not info["ok"],
        "online": True,
        "verified_by": row.get("verified_by", ""),
        "key_fp12": fingerprint_of(row.get("key", ""))[:12],
        "type": info.get("type", "") if info["ok"] else "",
    }


# ---------------------------------------------------------------- home: admin-pinned (static) entries
# For a server that has no lease code (e.g. an older release): listed by the home admin, not by signed pings.
# Online status comes from a small public HTTPS probe (cached PROBE_SECONDS); the icon is a file in PINNED_DIR
# that can be replaced at any time (the URL carries its hash, so browsers pick up a new one).
PINNED_DIR = os.path.join(STATE_DIR, "directory-pinned")
PROBE_SECONDS = 120
PINNED_ICON_MAX = 1024 * 1024   # admin-supplied file, so larger than the 64 KB limit for fetched icons
_probe = {}
_probe_lock = threading.Lock()


# Copies of the admin-pinned directory entries.
def _pinned_rows():
    with _lock:
        return [dict(r) for r in _load()["pinned"].values()]


# Probe a pinned server's health page now and remember whether it answered.
def _probe_now(row):
    try:
        _fetch_bytes(row["origin"], row.get("probe") or "/api/health", 65536, "*/*")
        ok = True
    except Exception:
        ok = False
    with _probe_lock:
        _probe[row["host"]] = {"at": time.time(), "online": ok, "busy": False}
    return ok


def pinned_online(row):
    """Cached probe result. First call probes inline; later stale results refresh in the background."""
    now = time.time()
    with _probe_lock:
        hit = _probe.get(row["host"])
        if hit and (now - hit["at"] < PROBE_SECONDS or hit.get("busy")):
            return hit["online"]
        if hit:
            hit["busy"] = True
    if not hit:
        return _probe_now(row)
    threading.Thread(target=_probe_now, args=(row,), daemon=True).start()
    return hit["online"]


# A pinned entry's picture from disk (size-checked), or (None, "").
def _pinned_icon(row):
    path = os.path.join(PINNED_DIR, row.get("icon_file") or "")
    try:
        if not row.get("icon_file") or os.path.getsize(path) > PINNED_ICON_MAX:
            return None, ""
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None, ""
    kind = _image_kind(raw, PINNED_ICON_MAX)
    return (raw, kind) if kind else (None, "")


# Which image type these bytes are (png, jpg, webp, gif), or "" if not an allowed picture or too big.
def _image_kind(raw, limit):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 12 or len(raw) > limit:
        return ""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return ""


# What the public directory shows for an admin-pinned entry.
def pinned_public_row(row):
    raw, _kind = _pinned_icon(row)
    return {
        "id": row["id"], "name": row.get("name", ""), "server": row["host"], "login_url": row["login_url"],
        "icon": ("/directory/icon/%s?h=%s" % (row["id"], icon_hash(raw))) if raw else "",
        "info_from_server": False, "online": pinned_online(row), "verified_by": "admin", "key_fp12": "",
        "pinned": True, "type": row.get("type", ""),
    }


def pin(server, name, kind, login_url, probe, icon_src=""):
    """Add or update an admin-pinned entry. icon_src is copied into PINNED_DIR as <host>.<ext>."""
    origin = norm_origin(server if "://" in server else "https://" + server, False)
    if not origin:
        return "The server must be https://host[:port]."
    login = (login_url or origin + "/").strip()
    if not login.startswith(origin + "/") or any(c in login for c in " \r\n\t\\\"'<>"):
        return "The login URL must be an https:// page on %s." % origin
    if not re.match(r"^/[A-Za-z0-9/_.-]{0,100}$", probe or "/"):
        return "The probe must be a path like /api/health."
    name = clean_name(name)
    if not name:
        return "A name is required."
    hk = host_key(origin)
    icon_file = ""
    if icon_src:
        try:
            with open(icon_src, "rb") as fh:
                raw = fh.read(PINNED_ICON_MAX + 1)
        except OSError as exc:
            return "Can't read the icon: %s" % exc
        ikind = _image_kind(raw, PINNED_ICON_MAX)
        if not ikind:
            return "The icon must be a PNG, JPEG or WebP up to %d KB." % (PINNED_ICON_MAX // 1024)
        os.makedirs(PINNED_DIR, exist_ok=True)
        icon_file = re.sub(r"[^A-Za-z0-9.-]", "_", hk) + "." + ikind
        dest = os.path.join(PINNED_DIR, icon_file)
        if os.path.abspath(icon_src) != os.path.abspath(dest):
            tmp = dest + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, dest)
    with _lock:
        store = _load()
        old = store["pinned"].get(hk) or {}
        store["pinned"][hk] = {"id": old.get("id") or secrets.token_hex(8), "host": hk, "origin": origin, "name": name,
                               "type": clean_name(kind or "")[:20], "login_url": login, "probe": probe or "/api/health",
                               "icon_file": icon_file or old.get("icon_file", ""), "added": old.get("added") or int(time.time())}
        _save(store)
    with _probe_lock:
        _probe.pop(hk, None)
    return ""


# Remove an admin-pinned entry; returns a message for manage.py.
def unpin(server):
    origin = norm_origin(server if "://" in server else "https://" + server, False)
    hk = host_key(origin) if origin else (server or "").strip().lower()
    with _lock:
        store = _load()
        if hk not in store["pinned"]:
            return "No pinned entry for %s." % hk
        store["pinned"].pop(hk)
        _save(store)
    return ""


# The public directory list: pinned entries first, then live verified listings.
def directory_list():
    now = time.time()
    pins = _pinned_rows()
    pinned_hosts = {r["host"] for r in pins}
    with _lock:
        store = _load()
        revoked = _revoked_fps(store)
        rows = [dict(r) for r in store["listings"].values() if _listed(r, now, revoked) and r.get("host") not in pinned_hosts]
    me = self_public_row()
    if me:
        rows = [r for r in rows if r.get("host") != me["server"]]
        pins = [r for r in pins if r.get("host") != me["server"]]
    out = [public_row(r, server_info(r)) for r in rows] + [pinned_public_row(r) for r in pins]
    out.sort(key=lambda r: (r["name"] or r["server"]).lower())
    return ([me] if me else []) + out


# directory-signin (Oct 21 2026): a home that is itself a public server (open sign-up / public Lobby, like
# branchchat.app) lists itself first as the "Public server". It needs no ping: it is the server answering. Works the
# same on any home (its own BC_PUBLIC_URL), and org servers that only report to a home are unaffected.
def self_public_row():
    if not HOME_MODE:
        return None
    try:
        import publicmode
        public = bool(publicmode.OPEN_SIGNUP or getattr(publicmode, "PUBLIC_MODE", False))
    except Exception:
        public = False
    if not public:
        return None
    origin = (norm_origin(bcconfig.PUBLIC_URL, True) or "").rstrip("/")
    if not origin:
        return None
    return {
        "id": "self", "name": "BranchChat Public server", "server": host_key(origin), "login_url": origin + "/?login",
        "icon": "/favicon.png", "info_from_server": False, "online": True, "verified_by": "self", "key_fp12": "",
        "type": "public", "public": True, "self": True,
    }


def lookup(server):
    """For the landing page: what the home knows about this server (verified listing or not)."""
    origin = norm_origin(server or "", ALLOW_HTTP)
    if not origin:
        return {"ok": False, "error": "That server address is not valid."}
    with _lock:
        store = _load()
        row = store["listings"].get(host_key(origin))
        revoked = _revoked_fps(store)
        row = dict(row) if row else None
    out = {"ok": True, "server": host_key(origin), "origin": origin, "allow_http": ALLOW_HTTP, "listing": None}
    pinned = next((r for r in _pinned_rows() if r["host"] == host_key(origin) and r["origin"] == origin), None)
    if pinned:
        out["listing"] = pinned_public_row(pinned)
        return out
    if row and (row.get("banned") or fingerprint_of(row.get("key", "")) in revoked):
        out["banned"] = True
    elif row and row.get("verified") and row.get("origin") == origin:
        pr = public_row(row, server_info(row))
        pr["online"] = _listed(dict(row, hidden=False), revoked=revoked)
        out["listing"] = pr
    return out


# The picture for one directory entry by its id, or (None, "").
def icon_file(listing_id):
    if not re.match(r"^[a-f0-9]{16}$", listing_id or ""):
        return None, ""
    with _lock:
        store = _load()
        revoked = _revoked_fps(store)
        row = next((dict(r) for r in store["listings"].values() if r.get("id") == listing_id), None)
        pinned = next((dict(r) for r in store["pinned"].values() if r.get("id") == listing_id), None)
    if pinned:
        raw, kind = _pinned_icon(pinned)
        return (raw, ICON_TYPES[kind]) if raw else (None, "")
    if not row or row.get("banned") or fingerprint_of(row.get("key", "")) in revoked:
        return None, ""
    info = server_info(row)
    return (info["icon"], ICON_TYPES[info["icon_type"]]) if info["icon"] else (None, "")


# ---------------------------------------------------------------- home: safe outbound fetch
class _PinnedHTTPS(http.client.HTTPSConnection):
    # HTTPS connection to a host name but a fixed, already-checked IP address.
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    # Connect to the checked IP while still verifying the certificate for the host name.
    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    # Plain HTTP connection to a fixed, already-checked IP address.
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    # Connect to the checked IP.
    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def _public_ip(host, port):
    """Resolve once and refuse private, loopback, link-local and similar addresses (no SSRF into a LAN)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for info in infos:
        ip = info[4][0]
        addr = ipaddress.ip_address(ip.split("%")[0])
        if ALLOW_PRIVATE or addr.is_global:
            return ip
    raise PermissionError("private address")


# ---------------------------------------------------------------- home: rate limits (memory only)
_hits = {}


# Sliding-window rate limit; returns seconds to wait (0 when allowed) and counts this hit.
def _rate(bucket, who, limit, window):
    now = time.time()
    with _lock:
        key = (bucket, who)
        rows = [t for t in _hits.get(key, []) if now - t < window]
        if len(rows) >= limit:
            _hits[key] = rows
            return int(window - (now - rows[0])) + 1
        rows.append(now)
        _hits[key] = rows
        if len(_hits) > 20000:
            for k in list(_hits)[:5000]:
                _hits.pop(k, None)
    return 0


# Rate limit by client address for directory endpoints.
def rate_ip(ip, bucket="lease", limit=30, window=3600):
    return _rate(bucket, ip or "?", limit, window)


# ---------------------------------------------------------------- home: signed lease intake
def accept_lease(data):
    """Check one signed ping. Returns (http status, reply). Stores only key, login origin, last ping (+ last ts)."""
    if not isinstance(data, dict) or data.get("v") != 2:
        return 400, {"error": "Unknown lease format (expected a signed v2 ping)."}
    if set(data) - {"v", "login_url", "ts", "online", "key", "sig"}:
        return 400, {"error": "Only login_url, ts, online, key and sig are accepted."}
    login_url, ts, key, sig = data.get("login_url"), data.get("ts"), data.get("key"), data.get("sig")
    online = data.get("online") is not False
    if not isinstance(login_url, str) or not isinstance(ts, int) or isinstance(ts, bool):
        return 400, {"error": "Missing login_url or ts."}
    if not isinstance(key, str) or not re.match(r"^[A-Za-z0-9_-]{43}$", key) or not isinstance(sig, str) or not re.match(r"^[A-Za-z0-9_-]{86}$", sig):
        return 400, {"error": "Missing key or signature."}
    origin = norm_origin(login_url, ALLOW_HTTP)
    if not origin or login_url.rstrip("/") != origin:
        return 400, {"error": "The login URL must be https://host[:port]/."}
    now = int(time.time())
    if abs(now - ts) > SKEW:
        return 400, {"state": "stale", "error": "The timestamp is more than %d s off. Check the server clock." % SKEW}
    if not verify_lease(key, sig, login_url, ts, online):
        return 403, {"state": "bad-signature", "error": "The signature does not match."}
    fp = fingerprint_of(key)
    hk = host_key(origin)
    with _lock:
        if fp in _revoked_fps(_load()):
            return 403, {"state": "revoked", "error": "This server key was revoked by the directory."}
    if _rate("lease-host", hk + "|" + fp, 3, 50):
        return 429, {"error": "Too many pings for this server.", "retry": 60}
    with _lock:
        store = _load()
        if fp in _revoked_fps(store):
            return 403, {"state": "revoked", "error": "This server key was revoked by the directory."}
        rows = store["listings"]
        row = rows.get(hk)
        if row and row.get("banned"):
            return 403, {"state": "banned", "error": "This server is not allowed in the directory."}
        same_key = bool(row) and hmac.compare_digest(row.get("key", ""), key)
        if same_key and ts <= int(row.get("last_ts") or 0):
            return 409, {"state": "replay", "error": "This ping was already seen."}
        if (row and row.get("verified") and not same_key and now - int(row.get("last_ping") or 0) <= TTL
                and fingerprint_of(row.get("key", "")) not in _revoked_fps(store)):
            return 409, {"state": "conflict", "error": "Another key holds this address. It frees up after that listing expires."}
        if not row and len(rows) >= LISTING_MAX + PENDING_MAX:
            return 503, {"error": "The directory is full."}
        need_verify = (not same_key or not row.get("verified")
                       or (row.get("verified_by") == "well-known" and now - int(row.get("verified_at") or 0) > REVERIFY_SECONDS))
        if same_key and row.get("verified_by") == "vouch":
            need_verify = False
    verified_by = row.get("verified_by", "") if (same_key and not need_verify) else ""
    pending_reason = ""
    if need_verify:
        try:
            doc = fetch_well_known(origin)
            if doc.get("identity_key") == key:
                verified_by = "well-known"
            else:
                return 403, {"state": "rejected", "error": "%s/.well-known/branchchat does not show this server's public key." % origin}
        except PermissionError:
            pending_reason = "The login URL is a private address, so the home cannot check it. An admin can vouch for the key."
        except (OSError, ValueError, http.client.HTTPException, ssl.SSLError) as exc:
            pending_reason = "The home could not read %s/.well-known/branchchat (%s)." % (origin, exc.__class__.__name__)
    with _lock:
        store = _load()
        rows = store["listings"]
        if fp in _revoked_fps(store):
            return 403, {"state": "revoked", "error": "This server key was revoked by the directory."}
        row = rows.get(hk) or {"id": secrets.token_hex(8), "host": hk, "first_seen": now}
        if row.get("key") and row.get("key") != key:
            row = {"id": row["id"], "host": hk, "hidden": row.get("hidden", False), "first_seen": now}   # new key for this address
        if same_key and ts <= int(row.get("last_ts") or 0):
            return 409, {"state": "replay", "error": "This ping was already seen."}
        row.update({"origin": origin, "key": key, "online": online, "last_ping": now, "last_ts": ts})
        if verified_by:
            row["verified"] = True
            row["verified_by"] = verified_by
            if verified_by == "well-known" and need_verify:
                row["verified_at"] = now
            row.pop("pending", None)
        else:
            row["verified"] = False
            row["pending"] = pending_reason
        rows[hk] = row
        for k in [k for k, r in rows.items() if not r.get("banned") and now - int(r.get("last_ping") or 0) > FORGET_SECONDS]:
            rows.pop(k, None)
        _save(store)
        listed = _listed(row, now, _revoked_fps(store))
    if not verified_by:
        return 200, {"state": "pending", "error": pending_reason, "key_fp": fp, "ttl": TTL}
    state = "listed" if listed else ("hidden" if row.get("hidden") else ("offline" if not online else "verified"))
    return 200, {"state": state, "key_fp": fp, "ttl": TTL, "every": PING_SECONDS}


# ---------------------------------------------------------------- home: admin (manage.py + LAN-only admin API)
def admin_rows():
    now = time.time()
    with _lock:
        store = _load()
        revoked = _revoked_fps(store)
        rows = [dict(r) for r in store["listings"].values()]
    out = []
    for r in sorted(rows, key=lambda r: r.get("host", "")):
        fp = fingerprint_of(r.get("key", ""))
        age = int(now - int(r.get("last_ping") or 0))
        status = ("REVOKED" if fp in revoked else "BANNED" if r.get("banned") else "hidden" if r.get("hidden")
                  else "listed" if _listed(r, now, revoked) else "pending" if not r.get("verified")
                  else "expired" if age > TTL else "offline")
        out.append({"server": r.get("host", ""), "status": status, "verified_by": r.get("verified_by", ""),
                    "last_ping_age": age, "key_fp": fp, "login_url": (r.get("origin") or "") + "/",
                    "pending": r.get("pending", "")})
    return out


# The revoked identity keys (for the directory admin tools).
def revoked_list():
    with _lock:
        return list(_load()["revoked"])


def revoke(fp_or_server, reason="", undo=False):
    """Revoke a key by fingerprint (or the key of a server). Delists at once; pings with that key are refused."""
    target = (fp_or_server or "").strip()
    with _lock:
        store = _load()
        fp = target
        if not re.match(r"^[A-Za-z0-9_-]{32}$", target):
            origin = norm_origin(target, True)
            row = store["listings"].get(host_key(origin)) if origin else None
            if not row or not row.get("key"):
                return "", "No listing with a key for %s." % target
            fp = fingerprint_of(row["key"])
        if undo:
            before = len(store["revoked"])
            store["revoked"] = [r for r in store["revoked"] if r.get("fp") != fp]
            if len(store["revoked"]) == before:
                return fp, "That key is not revoked."
        elif fp not in _revoked_fps(store):
            store["revoked"].append({"fp": fp, "at": int(time.time()), "reason": clean_name(reason)[:120]})
        _save(store)
    with _info_lock:
        _info.clear()
    return fp, ""


def admin_set(host, field, value, fp12="", name="", icon_src=""):
    """hide / ban (True or False) or vouch (pins the key whose fingerprint the admin compared out of band)."""
    origin = norm_origin(host, True)
    hk = host_key(origin) if origin else (host or "").strip().lower()
    with _lock:
        store = _load()
        row = store["listings"].get(hk)
        if field == "ban" and not row and value:
            row = {"id": secrets.token_hex(8), "host": hk, "origin": origin or ""}
        if not row:
            return "No listing for %s." % hk
        if field == "vouch":
            if not value:
                row["verified"] = False
                row.pop("verified_by", None)
            else:
                fp = fingerprint_of(row.get("key", ""))
                if len(fp12 or "") < 12 or not fp.startswith(fp12):
                    return "Give the first 12+ characters of the key_fp that %s shows in /.well-known/branchchat." % hk
                row["verified"] = True
                row["verified_by"] = "vouch"
                row["verified_at"] = int(time.time())
                row.pop("pending", None)
                if name:
                    if not clean_name(name):
                        return "That name is empty after cleaning."
                    row["admin_name"] = clean_name(name)
                if icon_src:
                    fname, err = _store_icon(hk, icon_src, "vouch-")
                    if err:
                        return err
                    row["admin_icon_file"] = fname
        elif field in ("hide", "ban"):
            row["hidden" if field == "hide" else "banned"] = bool(value)
        else:
            return "Unknown field."
        store["listings"][hk] = row
        _save(store)
    with _info_lock:
        _info.pop(hk, None)
    return ""


# Delete a listing completely (the server can list again with a fresh ping); returns a message.
def forget(host):
    origin = norm_origin(host if "://" in host else "https://" + host, True)
    hk = host_key(origin) if origin else (host or "").strip().lower()
    with _lock:
        store = _load()
        if hk not in store["listings"]:
            return "No listing for %s." % hk
        store["listings"].pop(hk)
        _save(store)
    return ""


# ---------------------------------------------------------------- home: admin API on its own LAN-only listener
# Contract: /workspace/research/admin-api-contract.md (copy in docs/DIRECTORY-ADMIN-API.md).
# Separate TLS listener (self-signed, the panel pins it), source-IP allowlist, Ed25519-signed requests (BCADM1).
ADMIN_LISTEN = bcconfig.get("BC_DIRECTORY_ADMIN_LISTEN", "").strip()          # e.g. 0.0.0.0:8767 ; empty = off
ADMIN_TLS_DIR = os.path.join(STATE_DIR, "admin-tls")
ADMIN_NONCE_SECONDS = 300
ADMIN_SKEW = 60
_admin_nonces = {}
_admin_version = {"v": ""}


# Networks allowed to reach the directory admin port (BC_DIRECTORY_ADMIN_ALLOW).
def _admin_allow():
    nets = []
    for item in bcconfig.get("BC_DIRECTORY_ADMIN_ALLOW", "").replace(" ", ",").split(","):
        if item.strip():
            try:
                nets.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError:
                pass
    return nets


# Hex SHA-256 of a raw 32-byte key (as the admin panel shows it), or "".
def key_fpr_hex(pub_b64):
    try:
        raw = base64.urlsafe_b64decode(pub_b64 + "=" * (-len(pub_b64) % 4))
    except (ValueError, TypeError):
        return ""
    return hashlib.sha256(raw).hexdigest() if len(raw) == 32 else ""


def admin_keys():
    """{key_id: b64url pub} from BC_ADMIN_PANEL_PUBKEYS="<key_id>:<b64u>,...". key_id must be sha256(raw)[:16]."""
    out = {}
    for item in bcconfig.get("BC_ADMIN_PANEL_PUBKEYS", "").replace(" ", ",").split(","):
        kid, _, pub = item.strip().partition(":")
        if re.match(r"^[0-9a-f]{16}$", kid) and re.match(r"^[A-Za-z0-9_-]{43}$", pub) and key_fpr_hex(pub)[:16] == kid:
            out[kid] = pub
    return out


# The exact bytes an admin panel request signs (method, path, time, nonce, body hash).
def admin_message(method, path, ts, nonce, body):
    return "\n".join(["BCADM1", method.upper(), path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode("utf-8")


def admin_auth(peer_ip, headers, method, path, body):
    """(http status, reason). 0 = allowed. The reason is only logged."""
    if headers.get("CF-Connecting-IP") or headers.get("X-Forwarded-For") or headers.get("Cf-Ray"):
        return 403, "came through a proxy"
    try:
        ip = ipaddress.ip_address(peer_ip)
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
    except ValueError:
        return 403, "bad peer"
    if not any(ip in n for n in _admin_allow()):
        return 403, "peer not in BC_DIRECTORY_ADMIN_ALLOW"
    kid, ts, nonce, sig = (headers.get("X-BC-Admin-Key") or "", headers.get("X-BC-Admin-Time") or "",
                           headers.get("X-BC-Admin-Nonce") or "", headers.get("X-BC-Admin-Sig") or "")
    keys = admin_keys()
    pub = None
    for k, v in keys.items():
        if hmac.compare_digest(k, kid):
            pub = v
    if not pub:
        return 401, "unknown key id"
    if not ts.isdigit() or abs(time.time() - int(ts)) > ADMIN_SKEW:
        return 401, "stale time"
    if not re.match(r"^[A-Za-z0-9_-]{22,128}$", nonce):
        return 401, "bad nonce"
    try:
        raw_sig = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        _ed().Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(pub + "=")).verify(
            raw_sig, admin_message(method, path, ts, nonce, body))
    except Exception:
        return 401, "bad signature"
    now = time.time()
    with _lock:
        for k in [k for k, t in _admin_nonces.items() if now - t > ADMIN_NONCE_SECONDS]:
            _admin_nonces.pop(k, None)
        if (kid, nonce) in _admin_nonces:
            return 401, "replayed nonce"
        _admin_nonces[(kid, nonce)] = now
    if _rate("admin-key", kid, 60, 60):
        return 429, "rate limit"
    return 0, kid


# Every listing for the admin panel, with its status and whether its key is revoked.
def admin_servers(include_revoked=False):
    now = int(time.time())
    with _lock:
        store = _load()
        revoked = {r.get("fp"): r for r in store["revoked"]}
        rows = [dict(r) for r in store["listings"].values()]
    out = []
    for r in sorted(rows, key=lambda r: r.get("host", "")):
        key = r.get("key", "")
        rv = revoked.get(fingerprint_of(key)) if key else None
        if rv and not include_revoked:
            continue
        age = now - int(r.get("last_ping") or 0)
        status = ("revoked" if rv else "hidden" if (r.get("hidden") or r.get("banned"))
                  else "listed" if _listed(r, now, set(revoked)) else "pending" if not r.get("verified")
                  else "expired" if age > TTL else "offline")
        with _info_lock:
            cached = _info.get(r.get("origin") or "")
        name = ((cached or {}).get("data") or {}).get("name", "") if isinstance(cached, dict) else ""
        out.append({"host": r.get("host", ""), "origin": r.get("origin", ""), "name": name or r.get("name", ""),
                    "status": status, "verified_by": r.get("verified_by", ""), "public_key": key,
                    "key_fpr": key_fpr_hex(key) if key else "", "first_seen": r.get("first_seen"),
                    "last_ping": r.get("last_ping"), "revoked": bool(rv),
                    "revoked_at": rv.get("at") if rv else None, "revoke_reason": rv.get("reason") if rv else None,
                    "banned": bool(r.get("banned")), "hidden": bool(r.get("hidden"))})
    return out


# Admin requests: find the listing named by "host", or return the error reply.
def _admin_row(data):
    host = data.get("host")
    if not isinstance(host, str) or not host.strip() or len(host) > 260:
        return None, (400, {"error": "host is required"})
    origin = norm_origin(host if "://" in host else "https://" + host, True)
    hk = host_key(origin) if origin else host.strip().lower()
    with _lock:
        row = _load()["listings"].get(hk)
    return (hk, row), None


# Route one signed admin panel request (health, list, hide, ban, vouch, revoke...) and return (status, reply).
def admin_dispatch(method, path, data):
    route, _, query = path.partition("?")
    if method == "GET" and route == "/admin/v1/health":
        return 200, {"ok": True, "version": _admin_version["v"], "api": 1, "home_admin_pubkey": public_key_b64(),
                     "time": int(time.time())}
    if method == "GET" and route == "/admin/v1/servers":
        inc = "include=revoked" in query.split("&")
        return 200, {"servers": admin_servers(inc)}
    if method != "POST" or not route.startswith("/admin/v1/servers/"):
        return 404, {"error": "not found"}
    action = route.rsplit("/", 1)[1]
    if action not in ("revoke", "unrevoke", "ban", "unban", "hide", "unhide"):
        return 404, {"error": "not found"}
    found, err = _admin_row(data)
    if err:
        return err
    hk, row = found
    if action in ("revoke", "unrevoke"):
        want = data.get("key_fpr")
        if not row or not row.get("key"):
            return 404, {"error": "unknown host"}
        if not isinstance(want, str) or not re.match(r"^[0-9a-f]{64}$", want):
            return 400, {"error": "key_fpr must be 64 hex"}
        if not hmac.compare_digest(key_fpr_hex(row["key"]), want):
            return 409, {"error": "key_fpr does not match the current key"}
        reason = data.get("reason") or ""
        if not isinstance(reason, str):
            return 400, {"error": "reason must be text"}
        fp, msg = revoke(row["key"] and fingerprint_of(row["key"]), reason[:200], undo=(action == "unrevoke"))
        if msg and action == "revoke":
            return 400, {"error": msg}
        if action == "revoke":
            at = next((r.get("at") for r in revoked_list() if r.get("fp") == fp), int(time.time()))
            return 200, {"ok": True, "host": hk, "revoked_at": at}
        return 200, {"ok": True, "host": hk, "was_revoked": not msg}
    if not row and action != "ban":
        return 404, {"error": "unknown host"}
    field, value = ("ban" if "ban" in action else "hide"), not action.startswith("un")
    msg = admin_set(hk, field, value)
    return (404, {"error": msg}) if msg else (200, {"ok": True, "host": hk, "action": action})


def _admin_tls_context():
    """Self-signed cert made once in STATE_DIR/admin-tls (key mode 600). The panel pins its SHA-256."""
    import ssl as _ssl
    key_p, crt_p = os.path.join(ADMIN_TLS_DIR, "key.pem"), os.path.join(ADMIN_TLS_DIR, "cert.pem")
    if not (os.path.exists(key_p) and os.path.exists(crt_p)):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization as ser
        from cryptography.hazmat.primitives.asymmetric import ec
        import datetime
        os.makedirs(ADMIN_TLS_DIR, mode=0o700, exist_ok=True)
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "branchchat-home-admin")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650)).sign(k, hashes.SHA256()))
        fd = os.open(key_p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(k.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()))
        with open(crt_p, "wb") as fh:
            fh.write(cert.public_bytes(ser.Encoding.PEM))
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(crt_p, key_p)
    with open(crt_p, "rb") as fh:
        der = _ssl.PEM_cert_to_DER_cert(fh.read().decode("ascii"))
    return ctx, hashlib.sha256(der).hexdigest()


# Fingerprint of the admin port's TLS certificate (to pin in the admin panel).
def admin_tls_fingerprint():
    return _admin_tls_context()[1]


def start_admin_listener(version=""):
    """Separate TLS port, never the one the Cloudflare tunnel points at. Off unless HOME_MODE and BC_DIRECTORY_ADMIN_LISTEN."""
    if not (HOME_MODE and ADMIN_LISTEN):
        return
    _admin_version["v"] = version
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    host, _, port = ADMIN_LISTEN.rpartition(":")
    ctx, tls_fpr = _admin_tls_context()

    class AdminHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "branchchat-admin"
        timeout = 15

        # Keep the admin port quiet in the server log.
        def log_message(self, fmt, *args):
            return

        # Send a JSON reply; with a nonce, also sign it so the admin panel knows it came from this server.
        def _reply(self, code, obj, nonce=""):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            if nonce:
                msg = ("BCHOM1\n%s\n%s" % (nonce, hashlib.sha256(raw).hexdigest())).encode("utf-8")
                self.send_header("X-BC-Home-Sig", b64u(_identity()[0].sign(msg)))
            self.end_headers()
            self.wfile.write(raw)

        # Check size, address and signature of an admin request, then dispatch it.
        def _handle(self, method):
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = -1
            if length < 0 or length > 16384:
                self.close_connection = True
                return self._reply(400, {"error": "bad length"})
            body = self.rfile.read(length) if length else b""
            code, kid = admin_auth(self.client_address[0], self.headers, method, self.path, body)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            bh = hashlib.sha256(body).hexdigest()[:16]
            if code:
                print("directory admin audit: %s peer=%s key=%s %s %s body=%s -> %d (%s)" % (
                    stamp, self.client_address[0], (self.headers.get("X-BC-Admin-Key") or "-")[:16], method,
                    self.path[:120], bh, code, kid), flush=True)
                err = {401: "bad signature", 403: "not allowed from this IP", 429: "rate limit"}[code]
                return self._reply(code, {"error": err})
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeError, ValueError):
                data = None
            if not isinstance(data, dict):
                code, out = 400, {"error": "body must be a JSON object"}
            else:
                code, out = admin_dispatch(method, self.path, data)
            print("directory admin audit: %s peer=%s key=%s %s %s body=%s -> %d" % (
                stamp, self.client_address[0], kid, method, self.path[:120], bh, code), flush=True)
            self._reply(code, out, self.headers.get("X-BC-Admin-Nonce") or "")

        # GET on the admin port.
        def do_GET(self):
            self._handle("GET")

        # POST on the admin port.
        def do_POST(self):
            self._handle("POST")

    class TLSServer(ThreadingHTTPServer):
        daemon_threads = True

        # Accept a connection and do the TLS handshake with a short timeout.
        def get_request(self):
            sock, addr = self.socket.accept()
            sock.settimeout(10)
            try:
                return ctx.wrap_socket(sock, server_side=True), addr
            except Exception:
                sock.close()
                raise OSError("tls handshake failed")

        # Ignore broken connections instead of printing tracebacks.
        def handle_error(self, request, client_address):
            return

    srv = TLSServer((host or "127.0.0.1", int(port)), AdminHandler)
    threading.Thread(target=srv.serve_forever, name="directory-admin", daemon=True).start()
    print("directory: admin API (TLS) on %s, allow %s, %d panel key(s), cert sha256 %s" % (
        ADMIN_LISTEN, ",".join(str(n) for n in _admin_allow()) or "nobody", len(admin_keys()), tls_fpr), flush=True)
