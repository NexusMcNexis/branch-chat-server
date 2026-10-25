"""Public-instance features for Branch Chat (branchchat.app).

Everything here is OFF unless the server is started with PUBLIC_MODE=1 (or one
of the individual flags below). With no flags set, every hook in server.py is
a no-op and a private server behaves exactly as before.

Flags (environment or the BC_CONFIG env file; "1/true/yes/on" means on):
  PUBLIC_MODE                 master switch: turns on every feature below unless
                              that feature is set explicitly to 0
  BC_OPEN_SIGNUP              open sign-up page (Sign up / Log in first screen)
  BC_LANGUAGE_FILTER          the "Hide mature language" setting is offered
  BC_LANGUAGE_FILTER_DEFAULT  that setting starts ON for accounts that never chose
  BC_REPORT_BLOCK             report and block (DMs, Lobby, profile), admin report list
  BC_FULL_ACCOUNT_DELETE      account deletion also removes the person's messages,
                              files and reactions, and asks for the 2FA code
  BC_LOGIN_LIMITS             failed sign-in limits per IP and per username
  BC_SIGNUP_PER_IP_HOUR=3  BC_SIGNUP_PER_IP_DAY=10
  BC_SIGNUP_GLOBAL_HOUR=30 BC_SIGNUP_GLOBAL_DAY=300
  BC_LOGIN_FAILS_PER_IP=20 BC_LOGIN_FAILS_PER_USER=10 (per 15 minutes)
  BC_TRUSTED_PROXIES          comma list of CIDRs whose CF-Connecting-IP header is
                              believed (the cloudflared tunnel). Default in public
                              mode: loopback + private ranges. Default otherwise: none.
  TURNSTILE_SITEKEY / TURNSTILE_SECRET (or TURNSTILE_SECRET_FILE)
                              optional Cloudflare Turnstile check on sign-up;
                              skipped while either is unset.

The server cannot read messages (end-to-end encryption). Language filtering is
done in the browser. A report only carries message text when the reporter's own
browser decrypted it and the reporter chose to include it; the server cannot
check that text against the ciphertext.

Standard library only. This module never imports server.py (it runs as
__main__); it reaches the running server module through the request handler.
"""
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request

import accounts
import bcconfig
import contentfilter

_TRUE = ("1", "true", "yes", "on")


# A public-mode switch: its own setting if set, else the default (often PUBLIC_MODE).
def _flag(name, default, *aliases):
    raw = bcconfig.get(name, "", *aliases).strip().lower()
    if not raw:
        return default
    return raw in _TRUE


PUBLIC_MODE = _flag("PUBLIC_MODE", False, "BC_PUBLIC_MODE")
OPEN_SIGNUP = _flag("BC_OPEN_SIGNUP", PUBLIC_MODE, "OPEN_SIGNUP")
LANGUAGE_FILTER = _flag("BC_LANGUAGE_FILTER", PUBLIC_MODE, "LANGUAGE_FILTER")
LANGUAGE_FILTER_DEFAULT = LANGUAGE_FILTER and _flag("BC_LANGUAGE_FILTER_DEFAULT", PUBLIC_MODE, "LANGUAGE_FILTER_DEFAULT")
REPORT_BLOCK = _flag("BC_REPORT_BLOCK", PUBLIC_MODE, "REPORT_BLOCK")
FULL_DELETE = _flag("BC_FULL_ACCOUNT_DELETE", PUBLIC_MODE, "FULL_ACCOUNT_DELETE")
LOGIN_LIMITS = _flag("BC_LOGIN_LIMITS", PUBLIC_MODE, "LOGIN_LIMITS")
# Public-server content filters (see contentfilter.py). Default on only with PUBLIC_MODE.
LOBBY_LINK_FILTER = _flag("BC_LOBBY_LINK_FILTER", PUBLIC_MODE, "LOBBY_LINK_FILTER")
LANGUAGE_LOCK = _flag("BC_LANGUAGE_LOCK", PUBLIC_MODE, "LANGUAGE_LOCK")
if LANGUAGE_LOCK:  # locked on: the setting is shown (greyed out), always on, and the server refuses "off"
    LANGUAGE_FILTER = True
    LANGUAGE_FILTER_DEFAULT = True
LOBBY_CHECK = bool(LOBBY_LINK_FILTER or LANGUAGE_LOCK)  # the server opens Lobby messages to filter them
FILTER_EVASION_LIMIT = max(1, bcconfig.get_int("BC_FILTER_EVASION_LIMIT", 3))
FILTER_EVASION_WINDOW = max(1, bcconfig.get_int("BC_FILTER_EVASION_WINDOW_MIN", 10)) * 60
FILTER_STRIP_LIMIT = max(1, bcconfig.get_int("BC_FILTER_STRIP_LIMIT", 5))
FILTER_STRIP_WINDOW = max(1, bcconfig.get_int("BC_FILTER_STRIP_WINDOW_MIN", 10)) * 60
FILTER_MUTE_SECONDS = max(0, bcconfig.get_int("BC_FILTER_MUTE_MIN", 30)) * 60
# "Host your own Branch Chat" link + /host explainer. Points at GitHub releases once they are public.
HOST_LINK = _flag("BC_HOST_LINK", PUBLIC_MODE, "HOST_LINK")
SERVER_RELEASES_URL = (bcconfig.get("BC_SERVER_RELEASES_URL", "https://github.com/NexusMcNexis/branch-chat-server/releases/latest").strip()
                       or "https://github.com/NexusMcNexis/branch-chat-server/releases/latest")
# Older server downloads were attached to the app releases; used until the server repo has a release.
SERVER_RELEASES_FALLBACK = bcconfig.get("BC_SERVER_RELEASES_FALLBACK", "https://github.com/NexusMcNexis/branch-chat-releases/releases").strip()
SERVER_RELEASES_ALL_URL = (bcconfig.get("BC_SERVER_RELEASES_ALL_URL", "").strip()
                           or re.sub(r"/latest/?$", "", SERVER_RELEASES_URL))
SERVER_DOCS_URL = bcconfig.get("BC_SERVER_DOCS_URL", "").strip()
HOST_CHECK_SECONDS = max(300, bcconfig.get_int("BC_HOST_CHECK_SECONDS", 3600))

SIGNUP_PER_IP_HOUR = bcconfig.get_int("BC_SIGNUP_PER_IP_HOUR", 3)
SIGNUP_PER_IP_DAY = bcconfig.get_int("BC_SIGNUP_PER_IP_DAY", 10)
SIGNUP_GLOBAL_HOUR = bcconfig.get_int("BC_SIGNUP_GLOBAL_HOUR", 30)
SIGNUP_GLOBAL_DAY = bcconfig.get_int("BC_SIGNUP_GLOBAL_DAY", 300)
LOGIN_WINDOW = 15 * 60
LOGIN_FAILS_PER_IP = bcconfig.get_int("BC_LOGIN_FAILS_PER_IP", 20)
LOGIN_FAILS_PER_USER = bcconfig.get_int("BC_LOGIN_FAILS_PER_USER", 10)

_DEFAULT_PROXIES = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"


# Parse a list of networks (commas or spaces); bad entries are skipped.
def _networks(text):
    out = []
    for item in (text or "").replace(" ", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return out


TRUSTED_PROXIES = _networks(bcconfig.get("BC_TRUSTED_PROXIES", _DEFAULT_PROXIES if PUBLIC_MODE else ""))
TURNSTILE_SITEKEY = bcconfig.get("TURNSTILE_SITEKEY", "", "BC_TURNSTILE_SITEKEY").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


# The Cloudflare Turnstile secret, read from the secrets file, never from plain config.
def _turnstile_secret():
    return bcconfig.get_secret("TURNSTILE_SECRET") or bcconfig.get_secret("BC_TURNSTILE_SECRET")


TURNSTILE = bool(TURNSTILE_SITEKEY and _turnstile_secret())

# Any feature on: server.py runs the hooks. All off: server.py never calls in here.
ANY = bool(OPEN_SIGNUP or LANGUAGE_FILTER or REPORT_BLOCK or FULL_DELETE or LOGIN_LIMITS or LOBBY_CHECK or HOST_LINK)
APP_PAGE = bool(LANGUAGE_FILTER or REPORT_BLOCK or FULL_DELETE or LOBBY_CHECK or HOST_LINK)
ASSET_V = "5"  # cache-buster for public.css/public.js/host.css; bumped for group report/block (3), the 26.10.2 profile cleanup (4)
               # and the BranchChat wordmark styles in host.css (5)

STATE_DIR = bcconfig.STATE_DIR
BLOCKS_PATH = os.path.join(STATE_DIR, "blocks.json")
REPORTS_PATH = os.path.join(STATE_DIR, "reports.json")
# 26.10.2: groups a person chose "Leave and block" on. Kept apart from blocks.json, whose writers only keep "blocks".
GROUP_BLOCKS_PATH = os.path.join(STATE_DIR, "group-blocks.json")
SIGNUP_LOG_PATH = os.path.join(STATE_DIR, "signup-limits.json")
FILTER_PATH = os.path.join(STATE_DIR, "filter-flags.json")

REPORT_REASONS = ("spam", "harassment", "hate", "sexual", "violence", "self-harm", "other")
AUTO_REASON = "filter"  # automatic reports from the content filters (not choosable by people)
REPORT_TEXT_MAX = 2000
REPORT_NOTE_MAX = 500
REPORTS_KEEP = 5000
BLOCKS_MAX = 500
GROUP_BLOCKS_MAX = 200  # 26.10.2: most groups one account can leave-and-block
GROUP_NAME_MAX = 80     # 26.10.2: longest group name a reporter may include (their browser decrypted it)

_lock = threading.Lock()
_login_fails = {}


def flags():
    """The settings as plain values, for manage.py status and the tests. No secrets."""
    return {
        "PUBLIC_MODE": PUBLIC_MODE, "BC_OPEN_SIGNUP": OPEN_SIGNUP, "BC_LANGUAGE_FILTER": LANGUAGE_FILTER,
        "BC_LANGUAGE_FILTER_DEFAULT": LANGUAGE_FILTER_DEFAULT, "BC_REPORT_BLOCK": REPORT_BLOCK,
        "BC_FULL_ACCOUNT_DELETE": FULL_DELETE, "BC_LOGIN_LIMITS": LOGIN_LIMITS,
        "BC_SIGNUP_PER_IP_HOUR": SIGNUP_PER_IP_HOUR, "BC_SIGNUP_PER_IP_DAY": SIGNUP_PER_IP_DAY,
        "BC_SIGNUP_GLOBAL_HOUR": SIGNUP_GLOBAL_HOUR, "BC_SIGNUP_GLOBAL_DAY": SIGNUP_GLOBAL_DAY,
        "BC_LOGIN_FAILS_PER_IP": LOGIN_FAILS_PER_IP, "BC_LOGIN_FAILS_PER_USER": LOGIN_FAILS_PER_USER,
        "BC_TRUSTED_PROXIES": ",".join(str(n) for n in TRUSTED_PROXIES), "TURNSTILE": TURNSTILE,
        "BC_LOBBY_LINK_FILTER": LOBBY_LINK_FILTER, "BC_LANGUAGE_LOCK": LANGUAGE_LOCK,
        "BC_FILTER_EVASION_LIMIT": FILTER_EVASION_LIMIT, "BC_FILTER_EVASION_WINDOW_MIN": FILTER_EVASION_WINDOW // 60,
        "BC_FILTER_STRIP_LIMIT": FILTER_STRIP_LIMIT, "BC_FILTER_STRIP_WINDOW_MIN": FILTER_STRIP_WINDOW // 60,
        "BC_FILTER_MUTE_MIN": FILTER_MUTE_SECONDS // 60, "BC_HOST_LINK": HOST_LINK, "BC_SERVER_RELEASES_URL": SERVER_RELEASES_URL,
    }


def _srv(handler):
    """The running server module (it is __main__ under systemd/docker)."""
    return sys.modules[type(handler).__module__]


# ---------------------------------------------------------------------------
# Client address
# ---------------------------------------------------------------------------

# The connecting address of a request (IPv4-mapped IPv6 turned back into IPv4), or None.
def _peer_ip(handler):
    raw = (handler.client_address or ("",))[0] if hasattr(handler, "client_address") else ""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def client_ip(handler):
    """The visitor's address. CF-Connecting-IP is believed only when the TCP peer is a trusted proxy."""
    peer = _peer_ip(handler)
    if peer is not None and any(peer in net for net in TRUSTED_PROXIES):
        header = (handler.headers.get("CF-Connecting-IP") or "").strip()
        if header:
            try:
                ip = ipaddress.ip_address(header)
                if ip.version == 6 and ip.ipv4_mapped:
                    ip = ip.ipv4_mapped
                return str(ip)
            except ValueError:
                pass
    return str(peer) if peer is not None else "unknown"


def _ip_tag(ip):
    """A keyed hash of the address, so the state files never hold raw IPs."""
    if ":" in ip:
        # IPv6: one person usually controls a whole /64.
        try:
            ip = str(ipaddress.ip_network(ip + "/64", strict=False).network_address) + "/64"
        except ValueError:
            pass
    return accounts.code_hash("signup-ip", ip)[:24]


# ---------------------------------------------------------------------------
# Small JSON stores
# ---------------------------------------------------------------------------

# Read a JSON file; the fallback when it is missing, broken or the wrong type.
def _read(path, fallback):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


# Write JSON atomically (temp file, fsync, rename), private to the server user.
def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ---------------------------------------------------------------------------
# Pages: login.html gets the Sign up form, index.html gets public.js
# ---------------------------------------------------------------------------

SIGNUP_FORM = """
<nav id="auth-tabs" class="auth-tabs" role="tablist" aria-label="Sign up or log in">
  <button id="tab-signup" type="button" role="tab" aria-selected="true" aria-controls="signup">Sign up</button>
  <button id="tab-login" type="button" role="tab" aria-selected="false" aria-controls="login">Log in</button>
</nav>
<form id="signup" novalidate>
  <div class="brand">
    <span class="bc-icon"><img src="/favicon.png" alt="" width="48" height="48"></span>
    <h1><span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span> <a class="ver" href="/patch-notes" title="Patch notes">__VER__</a></h1>
  </div>
  <p class="secure"><span class="led signup-led"></span><span class="signup-secure">Not a secure connection</span></p>
  <p>Create a free account. Messages are end-to-end encrypted.</p>
  <label for="su-username">Username</label>
  <input id="su-username" autocomplete="username" maxlength="32" autocapitalize="off" spellcheck="false" aria-describedby="su-username-hint">
  <p class="hint" id="su-username-hint">3 to 32 letters, numbers, dots, dashes or underscores.</p>
  <label for="su-email">Email</label>
  <input id="su-email" type="email" autocomplete="email" maxlength="200" aria-describedby="su-email-hint">
  <p class="hint" id="su-email-hint">For sign-in codes on a new device and password resets. Not shown to anyone unless you choose.</p>
  <label for="su-password">Password</label>
  <input id="su-password" type="password" autocomplete="new-password" maxlength="200" aria-describedby="su-password-hint">
  <p class="hint" id="su-password-hint">At least 8 characters (up to 200).</p>
  <label for="su-again">Repeat password</label>
  <input id="su-again" type="password" autocomplete="new-password" maxlength="200">
  <label class="su-check"><input id="su-agree" type="checkbox"> <span>I am 13 or older and I will keep it respectful. Abusive accounts are removed.</span></label>
  <div id="su-turnstile" class="su-turnstile" hidden></div>
  <button id="su-go" type="submit">Create account</button>
  <button id="su-to-login" class="linkish" type="button">Already have an account? Log in</button>
  <div class="msg" id="su-error" role="alert"></div>
</form>
"""


# The version label ("v...") found in a page, for the front door.
def _version_of(text):
    found = re.search(r'class="ver"[^>]*>(v[^<]+)<', text)
    return found.group(1) if found else ""


def page(name, body, csp, signup=None):
    """Add the public pieces to login.html / index.html. Other pages pass through.
    signup=False skips the Sign up form (the old download page at /get keeps its plain sign-in card)."""
    if name not in ("login.html", "index.html"):
        return body, csp
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return body, csp
    css = '<link rel="stylesheet" href="/static/public.css?v=%s">' % ASSET_V
    if name == "login.html" and OPEN_SIGNUP and signup is not False:
        text = text.replace('<html lang="en">', '<html lang="en" data-signup="1">', 1)
        text = text.replace("A private, invite-only chat for family and friends.",
                            "Free, end-to-end encrypted chat with friends and family.")
        head = '<script src="/static/scheme.js?v=2"></script>\n' + css
        if TURNSTILE:
            head += '\n<meta name="bc-turnstile" content="%s">' % _attr(TURNSTILE_SITEKEY)
            head += '\n<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async defer></script>'
            csp = csp.replace("script-src 'self'", "script-src 'self' https://challenges.cloudflare.com", 1)
            csp = csp.replace("frame-src ", "frame-src https://challenges.cloudflare.com ", 1)
            csp = csp.replace("connect-src 'self'", "connect-src 'self' https://challenges.cloudflare.com", 1)
        text = text.replace("</head>", head + "\n</head>", 1)
        text = text.replace("<body>", "<body>\n" + SIGNUP_FORM.replace("__VER__", _version_of(text)), 1)
        text = text.replace("</body>", '<script type="module" src="/static/public-login.js?v=%s"></script>\n</body>' % ASSET_V, 1)
    elif name == "index.html" and APP_PAGE:
        feats = [k for k, on in (("filter", LANGUAGE_FILTER), ("filter-locked", LANGUAGE_LOCK), ("report", REPORT_BLOCK),
                                 ("delete", FULL_DELETE), ("lobby-links", LOBBY_LINK_FILTER), ("lobby-check", LOBBY_CHECK),
                                 ("host", HOST_LINK)) if on]
        text = text.replace('<html lang="en"', '<html lang="en" data-bc-public="%s"' % " ".join(feats), 1)
        text = text.replace("</head>", css + "\n</head>", 1)
        marker = '<script type="module" src="/static/app.js'
        text = text.replace(marker, '<script type="module" src="/static/public.js?v=%s"></script>\n%s' % (ASSET_V, marker), 1)
        if HOST_LINK:
            text = text.replace("</head>", host_head() + "\n</head>", 1)
            text = text.replace('<html lang="en"', '<html lang="en" ' + host_attrs(), 1)
            text = text.replace('<a id="menu-about"', host_menu_row() + '      <a id="menu-about"', 1)
    elif name == "login.html" and HOST_LINK:
        pass
    else:
        return body, csp
    if name == "login.html" and HOST_LINK:
        text = text.replace("</head>", host_head() + "\n</head>", 1)
        text = text.replace('<html lang="en"', '<html lang="en" ' + host_attrs(), 1)
        text = text.replace("<body>", "<body>\n" + host_bar(), 1)
    return text.encode("utf-8"), csp


# Escape text for an HTML attribute.
def _attr(text):
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def route_open(h, method, path):
    """Routes before the session wall. True when this module answered."""
    if HOST_LINK and method == "GET" and path in ("/host", "/host/"):
        body = host_page().encode("utf-8")
        h.send_bytes(200, body, "text/html; charset=utf-8", [("Cache-Control", "no-cache"), ("Content-Security-Policy", HOST_CSP),
                                                             ("X-Content-Type-Options", "nosniff")])
        return True
    if (LANGUAGE_FILTER or LOBBY_CHECK) and method == "GET" and path == "/static/filter-data.json":
        h.send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "filter-data.json"),
                    "application/json; charset=utf-8", "no-cache")
        return True
    if HOST_LINK and method == "GET" and path == "/api/host-info":
        h.send_json(200, dict(host_info(), ok=True))
        return True
    if method == "POST" and path == "/api/signup":
        if not OPEN_SIGNUP:
            return False
        signup(h)
        return True
    if method == "POST" and path == "/api/signup/check":
        if not OPEN_SIGNUP:
            return False
        signup_check(h)
        return True
    return False


def route_user(h, method, path, user):
    """Signed-in routes. True when this module answered."""
    if LANGUAGE_FILTER and path == "/api/account/filter":
        if method == "GET":
            h.send_json(200, {"ok": True, "on": filter_on(h, user["username_key"])})
        elif method == "POST":
            data = h.read_json_body() or {}
            on = data.get("on")
            if not isinstance(on, bool):
                h.send_json(400, {"error": "That setting was not saved."})
            elif LANGUAGE_LOCK and not on:
                h.send_json(403, {"error": "The mature language filter is always on for the public server.", "on": True, "locked": True})
            else:
                set_filter(h, user["username_key"], on)
                h.send_json(200, {"ok": True, "on": on})
        else:
            return False
        return True
    if REPORT_BLOCK:
        if method == "POST" and path == "/api/block":
            block(h, user, True)
            return True
        if method == "POST" and path == "/api/unblock":
            block(h, user, False)
            return True
        if method == "GET" and path == "/api/blocks":
            h.send_json(200, {"ok": True, "blocked": blocked_names(user["username_key"])})
            return True
        if method == "POST" and path == "/api/report":
            report(h, user)
            return True
        if path.startswith("/api/admin/reports"):
            if not is_moderator(h, user):
                h.send_json(403, {"error": "Only an admin can see reports."})
                return True
            if method == "GET" and path == "/api/admin/reports":
                h.send_json(200, {"ok": True, "reports": report_rows(_srv(h))})
                return True
            if method == "POST" and path == "/api/admin/reports/status":
                report_status(h, user)
                return True
        # 26.10.2 group safety. Group chats are end-to-end encrypted, so the server can't filter their names or
        # messages; people report instead and the owner can close a group that breaks the rules.
        if method == "POST" and path == "/api/groups/leave-block":
            leave_and_block(h, user)
            return True
        if method == "POST" and path == "/api/groups/unblock":
            group_unblock(h, user)
            return True
        if method == "POST" and path == "/api/admin/groups/close":
            if not _srv(h).is_owner(user):
                h.send_json(403, {"error": "Only the server owner can close a group."})
                return True
            group_close(h, user)
            return True
    if LOBBY_CHECK and path.startswith("/api/admin/filter"):
        if not is_moderator(h, user):
            h.send_json(403, {"error": "Only an admin can see filter flags."})
            return True
        if method == "GET" and path == "/api/admin/filter":
            h.send_json(200, dict(filter_overview(), ok=True))
            return True
        if method == "POST" and path == "/api/admin/filter/unmute":
            data = h.read_json_body() or {}
            other = accounts.find_user(data.get("username") or "") if isinstance(data.get("username"), str) else None
            if not other:
                h.send_json(400, {"error": "That account was not found."})
                return True
            unmute(other["username_key"], user["username"])
            h.send_json(200, {"ok": True})
            return True
    return False


# Admins and the owner can see reports and filter flags.
def is_moderator(h, user):
    return bool(user) and (user.get("role") == "admin" or _srv(h).is_owner(user))


def sync_extra(h, user):
    """Added to /api/sync as "public" only when a feature is on."""
    key = user["username_key"]
    out = {"signup": OPEN_SIGNUP}
    if LANGUAGE_FILTER:
        out["filter"] = {"available": True, "on": filter_on(h, key), "locked": LANGUAGE_LOCK}
    if LOBBY_CHECK:
        until = muted_until(key)
        out["lobby_filter"] = {"links": LOBBY_LINK_FILTER, "language": LANGUAGE_LOCK, "muted_until": until or 0}
    if HOST_LINK:
        out["host"] = host_info()
    if REPORT_BLOCK:
        out["reports"] = True
        out["blocked"] = blocked_names(key)
        out["blocked_groups"] = sorted(group_blocks_of(key))  # 26.10.2: groups this person left and blocked
        if is_moderator(h, user):
            out["moderator"] = True
            out["open_reports"] = sum(1 for r in _read(REPORTS_PATH, {}).get("reports", []) if isinstance(r, dict) and r.get("status") == "open")
    if FULL_DELETE:
        out["full_delete"] = True
    return out


# ---------------------------------------------------------------------------
# Open sign-up and limits
# ---------------------------------------------------------------------------

# Sign-ups in the last 24 hours (for the sign-up rate limits).
def _signup_events(now):
    data = _read(SIGNUP_LOG_PATH, {})
    rows = data.get("events") if isinstance(data.get("events"), list) else []
    return [r for r in rows if isinstance(r, list) and len(r) == 2 and isinstance(r[0], (int, float)) and now - r[0] < 86400]


# A wait in friendly words ("5 minutes", "2 hours").
def _minutes(seconds):
    m = max(1, int((seconds + 59) // 60))
    return "%d minute%s" % (m, "" if m == 1 else "s") if m < 90 else "%d hours" % max(2, round(m / 60))


def signup_limit(tag, now, record=False):
    """None when this address may sign up now, else (status, message). record=True notes one sign-up."""
    with _lock:
        rows = _signup_events(now)
        mine = [r[0] for r in rows if r[1] == tag]
        hour_mine = [t for t in mine if now - t < 3600]
        hour_all = [r[0] for r in rows if now - r[0] < 3600]
        if SIGNUP_PER_IP_HOUR > 0 and len(hour_mine) >= SIGNUP_PER_IP_HOUR:
            wait = 3600 - (now - min(hour_mine))
            return 429, "Too many new accounts from your network. Try again in %s." % _minutes(wait)
        if SIGNUP_PER_IP_DAY > 0 and len(mine) >= SIGNUP_PER_IP_DAY:
            wait = 86400 - (now - min(mine))
            return 429, "Too many new accounts from your network today. Try again in %s." % _minutes(wait)
        if SIGNUP_GLOBAL_HOUR > 0 and len(hour_all) >= SIGNUP_GLOBAL_HOUR:
            return 429, "BranchChat is getting a lot of sign-ups right now. Try again in a little while."
        if SIGNUP_GLOBAL_DAY > 0 and len(rows) >= SIGNUP_GLOBAL_DAY:
            return 429, "BranchChat has reached today's sign-up limit. Try again tomorrow."
        if record:
            rows.append([int(now), tag])
            _write(SIGNUP_LOG_PATH, {"events": rows})
    return None


def turnstile_ok(token, ip):
    """Ask Cloudflare whether the widget token is real. Network trouble counts as a failure."""
    secret = _turnstile_secret()
    if not secret:
        return True
    if not isinstance(token, str) or not token or len(token) > 2048:
        return False
    form = {"secret": secret, "response": token}
    if ip and ip != "unknown":
        form["remoteip"] = ip
    req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read(65536).decode("utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and data.get("success") is True


def signup(h):
    """Create a member account without an invite and sign this browser in."""
    S = _srv(h)
    data = h.read_json_body()
    if not data:
        h.send_json(400, {"error": "That sign-up was not accepted."})
        return
    ip = client_ip(h)
    tag = _ip_tag(ip)
    now = time.time()
    limited = signup_limit(tag, now)
    if limited:
        h.send_json(limited[0], {"error": limited[1], "limited": True})
        return
    username = data.get("username").strip() if isinstance(data.get("username"), str) else ""
    email = data.get("email").strip() if isinstance(data.get("email"), str) else ""
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if not accounts.USERNAME_RE.match(username):
        h.send_json(400, {"error": "Usernames are 3 to 32 letters, numbers, dots, dashes or underscores, starting with a letter or number.", "field": "username"})
        return
    bad = name_refused(username, "username", None)
    if bad:
        h.send_json(400, {"error": bad, "field": "username"})
        return
    if not accounts.EMAIL_RE.match(email) or len(email) > 200 or any(ch in email for ch in "\r\n<>,"):
        h.send_json(400, {"error": "Enter a real email address.", "field": "email"})
        return
    if len(password) < 8 or len(password) > 200:
        h.send_json(400, {"error": "Use at least 8 characters.", "field": "password"})
        return
    if data.get("agree") is not True:
        h.send_json(400, {"error": "Tick the box to agree before creating an account.", "field": "agree"})
        return
    if TURNSTILE and not turnstile_ok(data.get("turnstile"), ip):
        h.send_json(400, {"error": "The human check did not pass. Try it again.", "field": "turnstile"})
        return
    key = username.lower()
    if key == bcconfig.OWNER_USERNAME and not accounts.find_user(key):
        # The owner name stays reserved for manage.py create-admin.
        h.send_json(400, {"error": "That username is already used.", "field": "username"})
        return
    hashed = accounts.hash_password(password)
    with accounts._lock:
        store = accounts._read()
        for row in store["users"]:
            if row.get("username_key") == key:
                h.send_json(400, {"error": "That username is already used.", "field": "username"})
                return
            if (row.get("email") or "").lower() == email.lower() and row.get("password"):
                h.send_json(400, {"error": "That email already has an account. Log in or reset the password.", "field": "email"})
                return
        for item in store["invites"]:
            if (item.get("username_key") or "") == key:
                h.send_json(400, {"error": "That username is already used.", "field": "username"})
                return
        # Count this sign-up only when it is about to succeed, and re-check inside the lock.
        limited = signup_limit(tag, time.time(), record=True)
        if limited:
            h.send_json(limited[0], {"error": limited[1], "limited": True})
            return
        store["users"].append({
            "id": secrets.token_hex(16),
            "username": username,
            "username_key": key,
            "email": email,
            "role": "member",
            "password": hashed,
            "session_version": 1,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "signup": {"ts": int(now), "ip": tag},
        })
        accounts._write(store)
    if LANGUAGE_FILTER:
        set_filter(h, key, LANGUAGE_FILTER_DEFAULT)
    user = accounts.find_user(key)
    if not user:
        h.send_json(400, {"error": "That account was not created."})
        return
    import featuremail  # imported here to keep publicmode's imports light
    featuremail.new_user(user)  # 26.10.4: queue the latest "What's new" email for this brand-new account
    token = accounts.make_session(user)
    h.send_json(200, {"ok": True, "username": user["username"]}, cookie=token)


_name_checks = {}
_name_checks_lock = threading.Lock()


def signup_check(h):
    """Front door step 1: is this username allowed and free? Creates nothing. Same answers /api/signup gives
    for the username, so it reveals nothing new. Limited to 40 checks per address per 10 minutes."""
    data = h.read_json_body() or {}
    tag = _ip_tag(client_ip(h))
    now = time.time()
    with _name_checks_lock:
        hits = [t for t in _name_checks.get(tag, []) if now - t < 600]
        if len(hits) >= 40:
            h.send_json(429, {"error": "Too many tries. Wait a few minutes.", "limited": True})
            return
        hits.append(now)
        _name_checks[tag] = hits
        if len(_name_checks) > 5000:
            for k in [k for k, v in _name_checks.items() if not v or now - v[-1] > 600]:
                _name_checks.pop(k, None)
    username = data.get("username").strip() if isinstance(data.get("username"), str) else ""
    if not accounts.USERNAME_RE.match(username):
        h.send_json(400, {"error": "Usernames are 3 to 32 letters, numbers, dots, dashes or underscores, starting with a letter or number.", "field": "username"})
        return
    bad = name_refused(username, "username", None)
    if bad:
        h.send_json(400, {"error": bad, "field": "username"})
        return
    key = username.lower()
    taken = key == bcconfig.OWNER_USERNAME and not accounts.find_user(key)
    if not taken:
        with accounts._lock:
            store = accounts._read()
        taken = any(row.get("username_key") == key for row in store["users"]) or \
            any((item.get("username_key") or "") == key for item in store["invites"])
    if taken:
        h.send_json(400, {"error": "That username is already used.", "field": "username"})
        return
    h.send_json(200, {"ok": True})


def login_gate(h, username):
    """True (and a 429 already sent) when this address or username failed too often."""
    if not LOGIN_LIMITS:
        return False
    now = time.time()
    ip_key = "ip:" + _ip_tag(client_ip(h))
    user_key = "user:" + (username or "").strip().lower()[:64] if isinstance(username, str) else "user:"
    with _lock:
        for k in list(_login_fails):
            _login_fails[k] = [t for t in _login_fails[k] if now - t < LOGIN_WINDOW]
            if not _login_fails[k]:
                _login_fails.pop(k, None)
        over = (LOGIN_FAILS_PER_IP > 0 and len(_login_fails.get(ip_key, [])) >= LOGIN_FAILS_PER_IP) or \
               (LOGIN_FAILS_PER_USER > 0 and user_key != "user:" and len(_login_fails.get(user_key, [])) >= LOGIN_FAILS_PER_USER)
        if over:
            oldest = min(_login_fails.get(ip_key, [now]) + _login_fails.get(user_key, [now]))
            wait = LOGIN_WINDOW - (now - oldest)
    if over:
        h.send_json(429, {"error": "Too many sign-in attempts. Wait %s and try again, or reset your password." % _minutes(wait), "limited": True})
        return True
    return False


# Count a failed sign-in against the address and the username (login limits).
def login_failed(h, username):
    if not LOGIN_LIMITS:
        return
    now = time.time()
    ip_key = "ip:" + _ip_tag(client_ip(h))
    keys = [ip_key]
    if isinstance(username, str) and username.strip():
        keys.append("user:" + username.strip().lower()[:64])
    with _lock:
        for k in keys:
            _login_fails.setdefault(k, []).append(now)


# ---------------------------------------------------------------------------
# Language filter preference (prefs.json row key "filter")
# ---------------------------------------------------------------------------

# Is the mature language filter on for this person (always, when the server locks it on).
def filter_on(h, key):
    if LANGUAGE_LOCK:
        return True
    S = _srv(h)
    row = S.load_prefs().get(key)
    if isinstance(row, dict) and isinstance(row.get("filter"), bool):
        return row["filter"]
    return LANGUAGE_FILTER_DEFAULT


# Save this person's mature language filter choice.
def set_filter(h, key, on):
    S = _srv(h)
    with S._store_lock:
        store = S.load_prefs()
        row = store.get(key)
        row = row if isinstance(row, dict) else {}
        row["filter"] = bool(on)
        store[key] = row
        S.atomic_write(S.PREFS_PATH, json.dumps(store))


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

# All person blocks as {account key: [blocked account keys]}; a broken file reads as empty.
def _blocks():
    data = _read(BLOCKS_PATH, {})
    rows = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    return {k: [x for x in v if isinstance(x, str)] for k, v in rows.items() if isinstance(k, str) and isinstance(v, list)}


def hidden_senders(key):
    """Account keys whose messages this person does not want to see. Empty when the feature is off."""
    if not REPORT_BLOCK:
        return set()
    return set(_blocks().get(key, []))


def blocked_either(a, b):
    """True when either account blocked the other."""
    if not REPORT_BLOCK or not a or not b:
        return False
    rows = _blocks()
    return b in rows.get(a, []) or a in rows.get(b, [])


def drop_blockers(targets, sender_key):
    """Push targets minus people who blocked the sender."""
    if not REPORT_BLOCK:
        return targets
    rows = _blocks()
    return [t for t in targets if sender_key not in rows.get(t, [])]


# Usernames this person blocked, sorted, for their Safety list.
def blocked_names(key):
    out = []
    for other in _blocks().get(key, []):
        found = accounts.find_user(other)
        out.append(found.get("username") if found else other)
    return sorted(out, key=str.lower)


# POST /api/block and /api/unblock. Blocking also ends the friendship and any pending request.
def block(h, user, on):
    S = _srv(h)
    data = h.read_json_body() or {}
    name = data.get("username") if isinstance(data.get("username"), str) else ""
    other = accounts.find_user(name) if name else None
    key = user["username_key"]
    if not other or not other.get("username_key"):
        h.send_json(400, {"error": "That account was not found."})
        return
    other_key = other["username_key"]
    if other_key == key:
        h.send_json(400, {"error": "You cannot block yourself."})
        return
    with S._store_lock:
        with _lock:
            rows = _blocks()
            mine = [x for x in rows.get(key, []) if x != other_key]
            if on:
                if len(mine) >= BLOCKS_MAX:
                    h.send_json(400, {"error": "You have blocked the most people allowed. Unblock someone first."})
                    return
                mine.append(other_key)
            if mine:
                rows[key] = mine
            else:
                rows.pop(key, None)
            _write(BLOCKS_PATH, {"blocks": rows})
        if on:
            # Blocking ends the friendship and any pending request, so no new DMs either way.
            social = S.load_social()
            pair = sorted([key, other_key])
            pairs = [p for p in social["pairs"] if not (isinstance(p, list) and sorted(p) == pair)]
            reqs = [r for r in social["requests"] if not (isinstance(r, dict) and {r.get("from"), r.get("to")} == {key, other_key})]
            if pairs != social["pairs"] or reqs != social["requests"]:
                social["pairs"], social["requests"] = pairs, reqs
                S.save_social(social)
    if on:
        try:
            import welcomebot
            welcomebot.removed(key, other_key)  # 26.10.3: blocking the Helper bot also stops it for good
        except Exception:
            pass
    h.send_json(200, {"ok": True, "blocked": blocked_names(key)})


# ---------------------------------------------------------------------------
# 26.10.2: group blocks ("Leave and block" a group)
# ---------------------------------------------------------------------------
# A group block means: I left this group and nobody may put me back in it, neither by adding me nor with an
# invite link. Blocking a person in a group uses the normal person block above (their messages are hidden
# everywhere, including groups, and the friendship ends, which is what lets someone add you to a group).

def _group_blocks():
    """All group blocks as {account key: [group id, ...]}. A broken file reads as empty."""
    data = _read(GROUP_BLOCKS_PATH, {})
    rows = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    return {k: [x for x in v if isinstance(x, str)] for k, v in rows.items() if isinstance(k, str) and isinstance(v, list)}


def group_blocks_of(key):
    """Group ids this account left and blocked. Empty when report/block is off on this server."""
    if not REPORT_BLOCK or not key:
        return set()
    return set(_group_blocks().get(key, []))


def group_blocked(key, gid):
    """True when this account blocked the group, so it must not be added back (server.py checks this)."""
    return bool(gid) and gid in group_blocks_of(key)


def group_add_refused(adder_key, target_key, gid):
    """Why adding target_key to group gid is refused, or "" when it is fine.
    Used by server.py for group create/add and invite links. Covers: the target left and blocked this group,
    or either person blocked the other (a blocked member can't pull you into a group)."""
    if not REPORT_BLOCK:
        return ""
    if gid and group_blocked(target_key, gid):
        return "blocked-group"
    if adder_key and adder_key != target_key and blocked_either(adder_key, target_key):
        return "blocked-person"
    return ""


def _set_group_block(key, gid, on):
    """Add or remove one group block for one account. Caller holds no locks; this takes _lock."""
    with _lock:
        rows = _group_blocks()
        mine = [x for x in rows.get(key, []) if x != gid]
        if on:
            if len(mine) >= GROUP_BLOCKS_MAX:
                mine = mine[1:]  # forget the oldest rather than refuse: leaving must always work
            mine.append(gid)
        if mine:
            rows[key] = mine
        else:
            rows.pop(key, None)
        _write(GROUP_BLOCKS_PATH, {"groups": rows})


def group_unblock(h, user):
    """POST /api/groups/unblock {id}: allow being added to that group again (Profile settings > Safety)."""
    data = h.read_json_body() or {}
    gid = (data.get("id") or "").lower() if isinstance(data.get("id"), str) else ""
    if not _srv(h).HEX32.match(gid):
        h.send_json(400, {"error": "That group was not found."})
        return
    _set_group_block(user["username_key"], gid, False)
    h.send_json(200, {"ok": True, "blocked_groups": sorted(group_blocks_of(user["username_key"]))})


def group_close(h, user):
    """POST /api/admin/groups/close {id}: the owner closes a reported group for everyone.
    The server can't read group names or messages (end-to-end encrypted), so closing is the tool for a group
    that breaks the rules. The group file is kept on disk marked deleted (with who closed it and when), so the
    operator can restore it by hand; members simply stop seeing it, the same as when the last member leaves."""
    S = _srv(h)
    data = h.read_json_body() or {}
    gid = (data.get("id") or "").lower() if isinstance(data.get("id"), str) else ""
    with S._store_lock:
        g = S.live_group(gid)
        if not g:
            h.send_json(404, {"error": "That group was not found or is already closed."})
            return
        g["deleted"] = True
        g["deleted_ts"] = int(time.time())
        g["closed_by"] = user["username"]
        S.save_group(g)
    # Mark every report about this group, so the list shows it was handled.
    with _lock:
        store = _read(REPORTS_PATH, {})
        rows = [r for r in store.get("reports", []) if isinstance(r, dict)]
        for r in rows:
            if (r.get("group") or {}).get("id") == gid and r.get("status") == "open":
                r["status"] = "resolved"
                r["resolved_by"] = user["username"]
                r["resolved_ts"] = int(time.time())
        _write(REPORTS_PATH, {"reports": rows})
    h.send_json(200, {"ok": True})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

# Strip control characters and trim a reporter's text to a length limit.
def _clean_text(value, limit):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()
    return value[:limit]


def report(h, user):
    """Store a report for the admins. Message text is only what the reporter chose to include.

    26.10.2 adds group reports. The body may carry "group" (a group id the reporter is in) with:
      - no username: report the whole group (the group itself is the problem);
      - a username: report one member of that group (optionally one of their group messages);
      - "group_name": the group's name as the reporter's browser decrypted it (the server can't check it);
      - "leave": true to leave the group and block it in the same step (it can't be rejoined or re-added).
    "block": true still blocks the reported person, which hides their messages in groups too."""
    S = _srv(h)
    data = h.read_json_body() or {}
    key = user["username_key"]
    name = data.get("username") if isinstance(data.get("username"), str) else ""
    other = accounts.find_user(name) if name else None
    # 26.10.2: which group (if any) this report is about. The reporter must be in it right now.
    gid = (data.get("group") or "").lower() if isinstance(data.get("group"), str) else ""
    group = None
    if gid:
        with S._store_lock:
            group = S.live_group(gid)
            if not group or key not in group["members"]:
                h.send_json(404, {"error": "That group was not found."})
                return
            if other and other.get("username_key") not in group["members"] and not data.get("message_id"):
                # Reporting a person "from this group" only makes sense while they are in it (a message
                # report is checked against the message's sender below instead).
                h.send_json(400, {"error": "That person is not in this group."})
                return
    if name and (not other or other.get("username_key") == key):
        h.send_json(400, {"error": "That account was not found."})
        return
    if other and other.get("bot"):
        # 26.10.3: the Helper bot is not a person, so it cannot be reported. Removing or muting it works like any friend.
        h.send_json(400, {"error": "The Helper bot isn't a person, so it can't be reported. You can mute it or remove it like any friend."})
        return
    if not other and not group:
        h.send_json(400, {"error": "That account was not found."})
        return
    reason = data.get("reason") if data.get("reason") in REPORT_REASONS else ""
    if not reason:
        h.send_json(400, {"error": "Pick a reason."})
        return
    context = None
    mid = (data.get("message_id") or "").lower() if isinstance(data.get("message_id"), str) else ""
    if mid:
        if not other or not S.HEX32.match(mid):
            h.send_json(400, {"error": "That message was not found."})
            return
        with S._store_lock:
            msg = next((m for m in S._messages if m.get("id") == mid), None)
            lobby = S.load_lobby()
            if not msg or not S.can_see(msg, key, lobby) or msg.get("sender") != other["username_key"]:
                h.send_json(404, {"error": "That message was not found."})
                return
            if group and msg.get("g") != group["id"]:
                h.send_json(404, {"error": "That message was not found."})
                return
            context = {"id": mid, "box": msg.get("box"), "seq": msg.get("seq"), "ts": msg.get("ts") or 0,
                       "deleted": bool(msg.get("deleted")), "has_file": bool(msg.get("blob"))}
            if msg.get("box") == "group" and not group:
                # A group message reported from the message menu: record its group too.
                group = S.live_group(msg.get("g"))
    group_info = None
    if group:
        # 26.10.2: what an admin may know about the group. The name is sealed (end-to-end encrypted); the
        # reporter's browser may send what it decrypted, marked unverified like included message text.
        group_info = {"id": group["id"], "members": len([m for m in group["members"].values() if isinstance(m, dict)]),
                      "created": int(group.get("created") or 0), "creator": S.name_of(group.get("creator") or ""),
                      "name": _clean_text(data.get("group_name"), GROUP_NAME_MAX)}
    now = int(time.time())
    with _lock:
        store = _read(REPORTS_PATH, {})
        rows = [r for r in store.get("reports", []) if isinstance(r, dict)] if isinstance(store.get("reports"), list) else []
        recent = [r for r in rows if r.get("reporter") == key and now - int(r.get("ts") or 0) < 3600]
        if len(recent) >= 10:
            h.send_json(429, {"error": "You have sent a lot of reports in the last hour. Try again later."})
            return
        if mid and any(r.get("reporter") == key and (r.get("message") or {}).get("id") == mid for r in rows):
            h.send_json(200, {"ok": True, "duplicate": True})
            return
        row = {
            "id": secrets.token_hex(8),
            "ts": now,
            "reporter": key,
            "reporter_name": user["username"],
            # A whole-group report has nobody in particular as "reported".
            "reported": other["username_key"] if other else "",
            "reported_name": (other.get("username") or other["username_key"]) if other else "",
            "reason": reason,
            "note": _clean_text(data.get("note"), REPORT_NOTE_MAX),
            "message": context,
            # Unverified: the reporter's browser decrypted this and chose to send it.
            "text": _clean_text(data.get("text"), REPORT_TEXT_MAX) if context else "",
            "group": group_info,
            "status": "open",
        }
        rows.append(row)
        if len(rows) > REPORTS_KEEP:
            rows = rows[-REPORTS_KEEP:]
        _write(REPORTS_PATH, {"reports": rows})
    blocked = False
    if other and data.get("block") is True:
        block_quiet(h, key, other["username_key"])
        blocked = True
    left = False
    if group and data.get("leave") is True:
        # 26.10.2 "Leave and block": block first, so nobody can re-add this person between the two steps.
        _set_group_block(key, group["id"], True)
        with S._store_lock:
            g = S.live_group(group["id"])
            if g and key in g["members"]:
                S.group_leave(g, key)
        left = True
    h.send_json(200, {"ok": True, "id": row["id"], "blocked": blocked, "left": left})


def leave_and_block(h, user):
    """POST /api/groups/leave-block {id}: leave a group and block it without filing a report (26.10.2).
    The block means no one can add this person back and invite links to it stop working for them."""
    S = _srv(h)
    data = h.read_json_body() or {}
    gid = (data.get("id") or "").lower() if isinstance(data.get("id"), str) else ""
    with S._store_lock:
        g = S.live_group(gid)
        if not g or user["username_key"] not in g["members"]:
            h.send_json(404, {"error": "That group was not found."})
            return
    _set_group_block(user["username_key"], gid, True)
    with S._store_lock:
        g = S.live_group(gid)
        if g and user["username_key"] in g["members"]:
            S.group_leave(g, user["username_key"])
    h.send_json(200, {"ok": True, "blocked_groups": sorted(group_blocks_of(user["username_key"]))})


def block_quiet(h, key, other_key):
    """Block without sending a response (used by report + block)."""
    S = _srv(h)
    with S._store_lock:
        with _lock:
            rows = _blocks()
            mine = [x for x in rows.get(key, []) if x != other_key]
            if len(mine) < BLOCKS_MAX:
                mine.append(other_key)
            rows[key] = mine
            _write(BLOCKS_PATH, {"blocks": rows})
        social = S.load_social()
        pair = sorted([key, other_key])
        social["pairs"] = [p for p in social["pairs"] if not (isinstance(p, list) and sorted(p) == pair)]
        social["requests"] = [r for r in social["requests"] if not (isinstance(r, dict) and {r.get("from"), r.get("to")} == {key, other_key})]
        S.save_social(social)


def report_rows(S=None):
    """Reports for the admin list, open first. S is the server module (used to look up groups, 26.10.2)."""
    rows = [r for r in _read(REPORTS_PATH, {}).get("reports", []) if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("status") != "open", -int(r.get("ts") or 0)))
    out = []
    for r in rows[:500]:
        target = accounts.find_user(r.get("reported") or "")
        out.append({
            "id": r.get("id"), "ts": r.get("ts"), "status": r.get("status"),
            "reporter": r.get("reporter_name") or "", "reported": r.get("reported_name") or "",
            "reported_exists": bool(target), "reported_disabled": bool(target and target.get("disabled")),
            "reported_since": (target or {}).get("created") or "",
            "reason": r.get("reason"), "note": r.get("note") or "", "text": r.get("text") or "",
            "message": r.get("message"), "resolved_by": r.get("resolved_by") or "",
            "against_count": sum(1 for x in rows if x.get("reported") and x.get("reported") == r.get("reported")),
            # 26.10.2: group reports carry the group (live = still open, so the owner can close it).
            "group": _group_row(r.get("group"), S),
        })
    return out


def _group_row(info, S=None):
    """Admin view of the group a report is about, or None. Adds whether it is still open and how many
    reports name it, so an admin can see a pattern even though the group's messages can't be read."""
    if not isinstance(info, dict) or not info.get("id"):
        return None
    live = S.live_group(info["id"]) if S is not None and hasattr(S, "live_group") else None
    every = [r for r in _read(REPORTS_PATH, {}).get("reports", []) if isinstance(r, dict)]
    return {"id": info["id"], "name": info.get("name") or "", "members": int(info.get("members") or 0),
            "creator": info.get("creator") or "", "created": int(info.get("created") or 0),
            "live": bool(live), "members_now": len(live["members"]) if live else 0,
            "reports": sum(1 for x in every if (x.get("group") or {}).get("id") == info["id"]),
            # 26.10.2: how many people left and blocked this group (a count only, never who), another signal for admins.
            "blocked_by": sum(1 for gids in _group_blocks().values() if info["id"] in (gids or []))}


# Admins mark a report resolved or open again.
def report_status(h, user):
    data = h.read_json_body() or {}
    rid = data.get("id") if isinstance(data.get("id"), str) else ""
    status = data.get("status") if data.get("status") in ("open", "resolved") else ""
    if not rid or not status:
        h.send_json(400, {"error": "That report was not updated."})
        return
    with _lock:
        store = _read(REPORTS_PATH, {})
        rows = [r for r in store.get("reports", []) if isinstance(r, dict)]
        hit = next((r for r in rows if r.get("id") == rid), None)
        if not hit:
            h.send_json(404, {"error": "That report was not found."})
            return
        hit["status"] = status
        hit["resolved_by"] = user["username"] if status == "resolved" else ""
        hit["resolved_ts"] = int(time.time()) if status == "resolved" else 0
        _write(REPORTS_PATH, {"reports": rows})
    h.send_json(200, {"ok": True})


# ---------------------------------------------------------------------------
# Full account deletion
# ---------------------------------------------------------------------------

def delete_account(h, user):
    """Password (and 2FA code when on), then remove the account and everything it owns.

    Removed: the login and every session, device keys, key backup and codes, push
    subscriptions, friends and requests, Lobby membership (the room key rotates),
    profile picture, display name, colour, contact details, preferences, blocks,
    every message the person sent (DM and Lobby: deleted for everyone, left as an
    anonymous "Message removed" marker), every file they uploaded, and their
    reactions. Reports other people filed about them stay for the admins; reports
    they filed lose their name.
    """
    S = _srv(h)
    data = h.read_json_body() or {}
    typed = data.get("username") if isinstance(data.get("username"), str) else ""
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    factor = data.get("factor") if isinstance(data.get("factor"), str) else ""
    if typed.strip().lower() != user["username_key"]:
        h.send_json(400, {"error": "Type your username to confirm."})
        return
    record = accounts.find_user(user["username"])
    if not record or not accounts.password_matches(password, record.get("password")):
        login_failed(h, user["username"])
        h.send_json(400, {"error": "That password was not accepted."})
        return
    if accounts.totp_enabled(record):
        if not factor.strip():
            h.send_json(400, {"error": "Enter a code from your authenticator app, or a backup code.", "need_totp": True})
            return
        if not accounts.consume_factor(user["username"], factor, allow_backup=True):
            h.send_json(400, {"error": "That authenticator code was not accepted.", "need_totp": True})
            return
    if user.get("role") == "admin":
        admins = [r for r in accounts.list_users() if r.get("role") == "admin" and r.get("ready") and not r.get("invite_id")]
        if len(admins) < 2:
            h.send_json(400, {"error": "The last admin account stays. Make someone else an admin first."})
            return
    if user["username_key"] == bcconfig.OWNER_USERNAME:
        h.send_json(400, {"error": "The owner account cannot be deleted here."})
        return
    error = accounts.remove_user(user["username"])
    if error:
        h.send_json(400, {"error": error})
        return
    key = user["username_key"]
    S.drop_account_data(key)
    counts = purge_content(h, key)
    h.send_json(200, {"ok": True, "deleted": counts}, clear_cookie=True)


def purge_content(h, key):
    """Delete what this account sent or uploaded. Returns counts."""
    S = _srv(h)
    blob_ids = set()
    removed = 0
    with S._store_lock:
        changed = False
        for msg in S._messages:
            if msg.get("sender") != key:
                continue
            if not msg.get("deleted"):
                blob = msg.get("blob") if isinstance(msg.get("blob"), dict) else None
                if blob and S.HEX32.match(blob.get("id") or ""):
                    blob_ids.add(blob["id"])
                    S._blob_seq.pop(blob["id"], None)
                msg["deleted"] = True
                msg["deleted_seq"] = S.next_deleted_seq()
                for field in ("iv", "ct", "wraps", "epoch_iv", "epoch_ct", "blob", "edited_ts", "edited_seq"):
                    msg.pop(field, None)
                removed += 1
            msg["sender"] = ""
            msg["sender_name"] = ""
            changed = True
        if changed:
            S.rewrite_messages_now()   # 26.10.8: ordered with queued appends; indexes refreshed
        store = S.load_reactions()
        touched = False
        for mid in list(store["items"]):
            rows = store["items"][mid]
            kept = [r for r in rows if r.get("user") != key]
            if len(kept) != len(rows):
                touched = True
                if kept:
                    store["items"][mid] = kept
                else:
                    store["items"].pop(mid, None)
        if touched:
            store["rev"] = int(store["rev"]) + 1
            S.save_reactions(store)
        lobby = S.load_lobby()
        lobby_changed = False
        for number, rows in list(lobby.get("epochs", {}).items()):
            if isinstance(rows, list):
                kept = [w for w in rows if not (isinstance(w, dict) and w.get("user") == key)]
                if len(kept) != len(rows):
                    lobby["epochs"][number] = kept
                    lobby_changed = True
        if lobby_changed:
            S.save_lobby(lobby)
        last = S.read_json(S.LAST_ACTIVE_PATH, {})
        if isinstance(last, dict) and key in last:
            last.pop(key, None)
            S.atomic_write(S.LAST_ACTIVE_PATH, json.dumps(last))
        S._presence.pop(key, None)
    # Files: every upload this account made, claimed or not.
    try:
        names = os.listdir(S.BLOB_DIR)
    except OSError:
        names = []
    for name in names:
        meta = S.blob_meta(name)
        if meta and meta.get("user") == key:
            blob_ids.add(name)
    for blob_id in blob_ids:
        if S.HEX32.match(blob_id):
            import shutil
            shutil.rmtree(os.path.join(S.BLOB_DIR, blob_id), ignore_errors=True)
    with _lock:
        rows = _blocks()
        rows.pop(key, None)
        for k in list(rows):
            rows[k] = [x for x in rows[k] if x != key]
            if not rows[k]:
                rows.pop(k, None)
        if os.path.exists(BLOCKS_PATH):
            _write(BLOCKS_PATH, {"blocks": rows})
        # 26.10.2: forget the groups this account left and blocked.
        grows = _group_blocks()
        if grows.pop(key, None) is not None:
            _write(GROUP_BLOCKS_PATH, {"groups": grows})
        store = _read(REPORTS_PATH, {})
        reps = [r for r in store.get("reports", []) if isinstance(r, dict)]
        hit = False
        for r in reps:
            if r.get("reporter") == key:
                r["reporter"] = ""
                r["reporter_name"] = "(deleted account)"
                hit = True
            if r.get("reported") == key:
                r["reported_deleted"] = True
                hit = True
        if hit:
            _write(REPORTS_PATH, {"reports": reps})
    with S._store_lock:
        prefs = S.load_prefs()
        if prefs.pop(key, None) is not None:
            S.atomic_write(S.PREFS_PATH, json.dumps(prefs))
    return {"messages": removed, "files": len(blob_ids)}


# ---------------------------------------------------------------------------
# Content filters: Lobby links + locked mature-language filter (public server only)
# ---------------------------------------------------------------------------
# Lobby messages are end-to-end encrypted with a per-message key that is wrapped to the Lobby epoch key, which
# the server never has. With LOBBY_CHECK on, the browser also sends that per-message key ("ck") with each Lobby
# post or edit. The server opens the message in memory, removes links / masks words, encrypts the result again
# with the same key and a fresh IV, stores that, and forgets the key. Members still open it with the wrapped key.
# Re-encrypting every time means a client cannot show members something other than what the server checked
# (a forged "ck" just makes the message unreadable for everyone else). DMs are untouched.

# The content filter's store (flags, recent evasion events, Lobby mutes), with safe empty parts.
def _filter_store():
    data = _read(FILTER_PATH, {})
    if not isinstance(data, dict):
        data = {}
    for k, empty in (("flags", []), ("events", {}), ("mutes", {})):
        if not isinstance(data.get(k), type(empty)):
            data[k] = empty
    return data


# Until when this person can't post in the Lobby (0 when not muted).
def muted_until(key):
    now = int(time.time())
    with _lock:
        row = _filter_store()["mutes"].get(key)
    until = int((row or {}).get("until") or 0) if isinstance(row, dict) else 0
    return until if until > now else 0


# An admin lifts someone's Lobby mute and clears their recent filter strikes.
def unmute(key, by):
    with _lock:
        data = _filter_store()
        data["mutes"].pop(key, None)
        ev = data["events"].get(key)
        if isinstance(ev, dict):
            ev["ev"] = []
            ev["strip"] = []
        data["flags"].append({"id": secrets.token_hex(6), "ts": int(time.time()), "user": key, "kind": "unmuted", "by": by})
        _write(FILTER_PATH, data)


# Current Lobby mutes and recent filter flags for the admin view.
def filter_overview():
    now = int(time.time())
    with _lock:
        data = _filter_store()
    mutes = []
    for key, row in data["mutes"].items():
        if isinstance(row, dict) and int(row.get("until") or 0) > now:
            found = accounts.find_user(key) or {}
            mutes.append({"username": found.get("username") or key, "until": int(row["until"]), "reason": row.get("reason") or ""})
    flags = []
    for f in reversed(data["flags"][-300:]):
        found = accounts.find_user(f.get("user") or "") or {}
        flags.append(dict(f, username=found.get("username") or f.get("name") or f.get("user") or ""))
    return {"mutes": mutes, "flags": flags, "thresholds": {
        "evasion_limit": FILTER_EVASION_LIMIT, "evasion_window_min": FILTER_EVASION_WINDOW // 60,
        "strip_limit": FILTER_STRIP_LIMIT, "strip_window_min": FILTER_STRIP_WINDOW // 60, "mute_min": FILTER_MUTE_SECONDS // 60}}


def _auto_report(key, name, summary, where, mid=None, ts=None):
    """Mirror a filter flag into the admins' Reports list (one open automatic report per person, updated)."""
    now = int(time.time())
    with _lock:
        store = _read(REPORTS_PATH, {})
        rows = [r for r in store.get("reports", []) if isinstance(r, dict)] if isinstance(store.get("reports"), list) else []
        hit = next((r for r in rows if r.get("reason") == AUTO_REASON and r.get("reported") == key and r.get("status") == "open"), None)
        if hit:
            hit["count"] = int(hit.get("count") or 1) + 1
            hit["ts"] = now
            hit["note"] = ("%s (%d flags)" % (summary, hit["count"]))[:REPORT_NOTE_MAX]
            if mid:
                hit["message"] = {"id": mid, "box": where, "seq": None, "ts": ts or now, "deleted": False, "has_file": False}
        else:
            rows.append({
                "id": secrets.token_hex(8), "ts": now, "reporter": "", "reporter_name": "Automatic filter",
                "reported": key, "reported_name": name, "reason": AUTO_REASON, "note": summary[:REPORT_NOTE_MAX],
                "message": {"id": mid, "box": where, "seq": None, "ts": ts or now, "deleted": False, "has_file": False} if mid else None,
                "text": "", "status": "open", "count": 1,
            })
            if len(rows) > REPORTS_KEEP:
                rows = rows[-REPORTS_KEEP:]
        _write(REPORTS_PATH, {"reports": rows})


def _summary(report):
    """What moderation keeps: kinds, techniques, link host names and masked words. Never the raw text or a full URL."""
    parts = []
    hosts = sorted({h["host"] for h in report.get("links", []) if h.get("host")})[:5]
    words = sorted({h["mask"] for h in report.get("words", [])})[:5]
    tech = sorted({t for h in report.get("links", []) + report.get("words", []) if h.get("evasion") for t in h.get("tech", [])})
    if report.get("links"):
        parts.append("%d link(s)%s" % (len(report["links"]), (": " + ", ".join(hosts)) if hosts else ""))
    if report.get("words"):
        parts.append("%d masked word(s): %s" % (len(report["words"]), ", ".join(words)))
    if tech:
        parts.append("evasion: " + ", ".join(tech))
    return "; ".join(parts), hosts, words, tech


def note_filter(user, report, where, moderator=False, mid=None):
    """Count strips and evasions for this person; flag for moderators; auto-mute in the Lobby past the limits.
    Returns {"flagged": bool, "muted_until": int}."""
    key = user["username_key"]
    name = user.get("username") or key
    now = int(time.time())
    evasion = any(h.get("evasion") for h in report.get("links", []) + report.get("words", []))
    stripped = bool(report.get("links"))
    if not evasion and not stripped and not report.get("words"):
        return {"flagged": False, "muted_until": 0}
    summary, hosts, words, tech = _summary(report)
    reasons = []
    muted = 0
    with _lock:
        data = _filter_store()
        ev = data["events"].get(key)
        ev = ev if isinstance(ev, dict) else {}
        ev_list = [t for t in ev.get("ev", []) if now - int(t) < FILTER_EVASION_WINDOW]
        st_list = [t for t in ev.get("strip", []) if now - int(t) < FILTER_STRIP_WINDOW]
        if evasion:
            ev_list.append(now)
            reasons.append("evasion")
        if stripped:
            st_list.append(now)
            if len(st_list) >= FILTER_STRIP_LIMIT:
                reasons.append("repeated link strips (%d in %d min)" % (len(st_list), FILTER_STRIP_WINDOW // 60))
        data["events"][key] = {"ev": ev_list[-50:], "strip": st_list[-50:]}
        if evasion and len(ev_list) >= FILTER_EVASION_LIMIT and FILTER_MUTE_SECONDS and not moderator:
            muted = now + FILTER_MUTE_SECONDS
            data["mutes"][key] = {"until": muted, "reason": "%d evasion flags in %d min" % (len(ev_list), FILTER_EVASION_WINDOW // 60), "ts": now}
            reasons.append("auto-muted in the Lobby for %d min" % (FILTER_MUTE_SECONDS // 60))
        if reasons:
            data["flags"].append({"id": secrets.token_hex(6), "ts": now, "user": key, "kind": where, "reasons": reasons,
                                  "tech": tech, "hosts": hosts, "words": words, "message": mid or ""})
            data["flags"] = data["flags"][-2000:]
        _write(FILTER_PATH, data)
    if reasons:
        _auto_report(key, name, "%s: %s. %s" % (where.capitalize(), ", ".join(reasons), summary), "lobby" if where == "lobby" else where, mid)
    return {"flagged": bool(reasons), "muted_until": muted}


# Decode base64 of an allowed length, or None.
def _b64(text, lo, hi):
    import base64
    if not isinstance(text, str) or not (lo <= len(text) <= hi):
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        return None


def lobby_check(h, user, data, edit=False):
    """Filter one Lobby post or edit. Returns (new_iv, new_ct, info) or raises nothing: on refusal returns
    (None, None, (code, message))."""
    import base64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    S = _srv(h)
    key = user["username_key"]
    moderator = is_moderator(h, user)
    until = muted_until(key)
    if until and not moderator:
        mins = max(1, (until - int(time.time()) + 59) // 60)
        return None, None, (403, "You can't post in the Lobby for %d more minute%s: too many messages tried to get around the filter. "
                                 "A moderator can lift this." % (mins, "" if mins == 1 else "s"))
    ck = _b64(data.get("ck"), 40, 64)
    iv = _b64(data.get("iv"), 8, 64)
    ct = _b64(data.get("ct"), 16, S.TEXT_CT_MAX)
    if not ck or len(ck) != 32:
        return None, None, (400, "Reload the page to send in the Lobby: this public server checks Lobby messages and needs the latest version.")
    if not iv or not ct:
        return None, None, (400, "That message was not sent.")
    aes = AESGCM(ck)
    try:
        payload = json.loads(aes.decrypt(iv, ct, None).decode("utf-8"))
    except Exception:
        return None, None, (400, "That message was not sent.")
    if not isinstance(payload, dict):
        return None, None, (400, "That message was not sent.")
    report = {"links": [], "words": []}
    text = payload.get("text")
    if isinstance(text, str) and text:
        payload["text"], rep = contentfilter.filter_text(text, links=LOBBY_LINK_FILTER, language=LANGUAGE_LOCK)
        report["links"] += rep["links"]
        report["words"] += rep["words"]
    f = payload.get("file")
    if isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"]:
        f["name"], rep = contentfilter.filter_text(f["name"], links=False, language=LANGUAGE_LOCK)
        report["words"] += rep["words"]
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    new_iv = os.urandom(12)
    new_ct = aes.encrypt(new_iv, plain, None)
    del ck, aes
    out_ct = base64.b64encode(new_ct).decode("ascii")
    if len(out_ct) > S.TEXT_CT_MAX:
        return None, None, (400, "That message is too long.")
    info = {"links": len(report["links"]), "words": len(report["words"]),
            "evasion": any(x.get("evasion") for x in report["links"] + report["words"]), "report": report}
    return base64.b64encode(new_iv).decode("ascii"), out_ct, info


def lobby_filtered(user, info, moderator, mid):
    """After the row is stored: flags and mutes. Returns what the browser is told."""
    res = note_filter(user, info["report"], "lobby", moderator=moderator, mid=mid)
    return {"links": info["links"], "words": info["words"], "flagged": res["flagged"], "muted_until": res["muted_until"]}


def name_refused(name, what, user, moderator=False):
    """Usernames, display names (and group names / statuses where a server has them): None, or the error to show."""
    if not (LANGUAGE_LOCK or LOBBY_LINK_FILTER) or not isinstance(name, str) or not name.strip():
        return None
    prob = contentfilter.name_problem(name, links=LOBBY_LINK_FILTER)
    if not prob or (prob["kind"] == "language" and not LANGUAGE_LOCK):
        return None
    if prob.get("evasion") and user:
        rep = {"links": [], "words": []}
        if prob["kind"] == "link":
            rep["links"] = [{"host": "", "evasion": True, "tech": prob["tech"]}]
        else:
            rep["words"] = [{"mask": contentfilter.mask_words(name)[0][:40], "evasion": True, "tech": prob["tech"]}]
        note_filter(user, rep, what, moderator=moderator)
    if prob["kind"] == "link":
        return "Links aren't allowed in %ss on the public server." % what
    return "That %s isn't allowed on the public server. Pick another one." % what


# Is this invite username refused by the name filter (returns the reason or None).
def _invite_name_check(username):
    return name_refused(username, "username", None)


if LANGUAGE_LOCK or LOBBY_LINK_FILTER:
    accounts.NAME_CHECK = _invite_name_check


# ---------------------------------------------------------------------------
# "Host your own Branch Chat" (public server only): link to GitHub releases, /host explainer, pop-up
# ---------------------------------------------------------------------------
HOST_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_host_state = {"available": False, "checked": 0.0, "tag": "", "url": "", "busy": False}
_HOST_FORCE = bcconfig.get("BC_HOST_AVAILABLE", "").strip().lower()  # tests: "1" / "0" skip the GitHub check

# One list for /host and the pop-up. Only what the repo really has: server/install.sh (native or Docker),
# server/docker-compose.yml + the multi-arch Dockerfile. Windows/Mac via Docker Desktop is untested: coming soon.
HOST_PLATFORMS = [
    {"id": "linux-docker", "title": "Linux \u00b7 Docker Compose", "ready": True,
     "line": "Any 64-bit Linux with Docker. One container, data in one folder.",
     "cmd": "cd branch-chat-server-*-docker/server && docker compose up -d",
     "req": "64-bit Linux (amd64 or arm64), Docker Engine with Compose, 1 GB RAM, 2 GB disk plus your photos and files",
     "steps": ["Download the Docker Compose package (branch-chat-server-<version>-docker-compose.tar.gz) from GitHub and unpack it.", "Copy server/config.env.example to server/config.env and set your address, name and admin.",
               "Run the command above, then open your address and sign in.", "Or get the installer package and run sudo ./server/install.sh --mode docker to have this set up for you."]},
    {"id": "linux-native", "title": "Linux \u00b7 plain install", "ready": True,
     "line": "A system service on Debian or Ubuntu. Lightest option, no Docker.",
     "cmd": "sudo ./server/install.sh",
     "req": "Debian 11+, Ubuntu 22.04+ or Raspberry Pi OS (64-bit), Python 3.9+ (installed for you), 1 GB RAM, 2 GB disk",
     "steps": ["Download the installer package (branch-chat-server-<version>-install.tar.gz) from GitHub, unpack it and open the folder.", "Run the installer. It asks six questions: install method, your group, admin account, access, email and backups.",
               "Open your address, sign in and invite people from the menu."]},
    {"id": "pi", "title": "Raspberry Pi", "ready": True,
     "line": "Pi 4 or 5 on 64-bit Raspberry Pi OS: the same installer, or Docker (arm64).",
     "cmd": "sudo ./server/install.sh",
     "req": "Raspberry Pi 4 or 5 (2 GB RAM or more), Raspberry Pi OS 64-bit, a USB SSD or a good A2 SD card",
     "steps": ["Flash Raspberry Pi OS (64-bit) and connect it to your network.", "Download the installer package from GitHub on the Pi, unpack it and open the folder.",
               "Run the installer and pick native (lightest) or docker."]},
    {"id": "proxmox", "title": "Proxmox", "ready": True,
     "line": "Make a small Debian 12 VM or LXC container, then follow the Linux steps inside it.",
     "cmd": "",
     "req": "Proxmox VE 7 or 8, a Debian 12 VM or LXC with 1 vCPU, 1 GB RAM and 8 GB disk",
     "steps": ["Create a Debian 12 VM (or an unprivileged LXC with nesting on if you want Docker).", "Inside it, follow Linux \u00b7 plain install or Linux \u00b7 Docker Compose.",
               "Back up the VM with your usual Proxmox backup job; the installer also keeps nightly backups."]},
    {"id": "desktop", "title": "Windows or Mac \u00b7 Docker Desktop", "ready": False,
     "line": "The same Docker image should run under Docker Desktop. Not tested yet, so it's coming soon.",
     "cmd": "", "req": "Docker Desktop (WSL 2 on Windows; Apple Silicon or Intel Mac)", "steps": []},
]


# Ask GitHub whether a server release exists yet (so Host your own can show downloads or "coming soon").
def _server_release(owner, repo, headers):
    """Newest non-draft, non-prerelease release of owner/repo with a server install file: (tag, page) or None."""
    req = urllib.request.Request("https://api.github.com/repos/%s/%s/releases?per_page=40" % (owner, repo), headers=headers)
    with urllib.request.urlopen(req, timeout=8) as res:
        rels = json.loads(res.read(4000000).decode("utf-8")) if res.status == 200 else []
    for info in rels if isinstance(rels, list) else []:
        if not isinstance(info, dict) or info.get("draft") or info.get("prerelease"):
            continue
        names = [str(a.get("name") or "") for a in info.get("assets") or [] if isinstance(a, dict)]
        if any(re.match(r"^branch-chat-server-.+-install\.tar\.gz$", n) for n in names):
            tag = re.sub(r"[^A-Za-z0-9._-]", "", str(info.get("tag_name") or ""))[:40]
            page = str(info.get("html_url") or "")
            if tag and page.startswith("https://github.com/"):
                return tag, page
    return None


# Ask GitHub where the newest server downloads are: the server repo first, then the old app-release fallback.
# App-only releases have no server files, so they are skipped.
def _host_check():
    ok, tag, url = False, "", ""
    headers = {"User-Agent": "BranchChat-host-check", "Accept": "application/vnd.github+json"}
    try:
        for u in (SERVER_RELEASES_URL, SERVER_RELEASES_FALLBACK):
            m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases", u or "")
            if not m:
                continue
            try:
                found = _server_release(m.group(1), m.group(2), headers)
            except Exception:
                found = None
            if found:
                ok, (tag, url) = True, found
                break
        if not ok and SERVER_RELEASES_URL.startswith("https://") and "github.com/" not in SERVER_RELEASES_URL:
            req = urllib.request.Request(SERVER_RELEASES_URL, method="HEAD", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as res:
                ok = 200 <= res.status < 300
    except Exception:
        ok = False
    finally:
        _host_state.update(available=ok, tag=tag if ok else "", url=url if ok else "", checked=time.time(), busy=False)


def host_info():
    """Is the releases page public yet? Checked at most once per BC_HOST_CHECK_SECONDS, in the background."""
    if _HOST_FORCE in ("1", "0"):
        return {"available": _HOST_FORCE == "1", "latest": SERVER_RELEASES_URL, "all": SERVER_RELEASES_ALL_URL, "tag": "", "checked": 0}
    now = time.time()
    if now - _host_state["checked"] > HOST_CHECK_SECONDS and not _host_state["busy"]:
        _host_state["busy"] = True
        threading.Thread(target=_host_check, name="host-check", daemon=True).start()
    return {"available": bool(_host_state["available"]), "latest": _host_state.get("url") or SERVER_RELEASES_URL, "all": SERVER_RELEASES_ALL_URL,
            "tag": _host_state["tag"], "checked": int(_host_state["checked"])}


# Escape text for HTML.
def _e(text):
    return _attr(str(text))


_HOST_ICON = ('<svg class="bc-host-ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3.5" y="4" width="17" height="6.5" rx="1.6"/>'
              '<rect x="3.5" y="13.5" width="17" height="6.5" rx="1.6"/><path d="M7 7.25h.01M7 16.75h.01M11 7.25h5M11 16.75h5"/></svg>')
_EXT_ICON = '<svg class="bc-host-ext" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>'


# The CSS and script tags for the Host your own dialog.
def host_head():
    return '<link rel="stylesheet" href="/static/host.css?v=%s">\n<script type="module" src="/static/host.js?v=%s"></script>' % (ASSET_V, ASSET_V)


# Marks the page with whether server downloads are ready or coming soon.
def host_attrs():
    info = host_info()
    return 'data-bc-host="%s"' % ("ready" if info["available"] else "soon")


# Link attributes for Host your own: straight to GitHub when ready, else our /host page.
def _host_link_attrs(info):
    if info["available"]:
        return 'href="%s" target="_blank" rel="noopener"' % _e(info["latest"])
    return 'href="/host"'


def host_bar():
    """Login / sign-up page: slim link at the top."""
    info = host_info()
    sub = "" if info["available"] else '<span class="bc-host-sub">Server downloads coming soon</span>'
    return ('<nav class="bc-host-bar" aria-label="Self-hosting"><a class="bc-host-link" id="bc-host-top" %s>%s<span class="bc-host-txt">'
            'Host your own BranchChat</span>%s%s</a></nav>' % (_host_link_attrs(info), _HOST_ICON, sub, _EXT_ICON if info["available"] else ""))


def host_menu_row():
    """In the app's menu, above About: a permanent row (same look as Invite)."""
    info = host_info()
    sub = "Server downloads on GitHub" if info["available"] else "Server downloads coming soon"
    return ('<a id="menu-host" class="menu-row menu-host" %s><span class="menu-row-ico" aria-hidden="true">%s</span>'
            '<span class="menu-row-label">Host your own BranchChat<small class="menu-host-sub">%s</small></span>'
            '<span class="menu-row-chev" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M9 5.5l6.5 6.5L9 18.5"/></svg></span></a>\n'
            % (_host_link_attrs(info), _HOST_ICON, sub))


# One line per platform (Linux, Raspberry Pi, Proxmox...) for the Host your own dialog.
def _platform_lines(compact):
    out = []
    for p in HOST_PLATFORMS:
        status = "" if p["ready"] else ' <span class="bc-host-soon">Coming soon</span>'
        cmd = ('<code>%s</code>' % _e(p["cmd"])) if (p["cmd"] and compact) else ""
        out.append('<li class="bc-host-plat%s" data-plat="%s"><b>%s</b>%s<span>%s</span>%s</li>'
                   % ("" if p["ready"] else " is-soon", p["id"], _e(p["title"]), status, _e(p["line"]), cmd))
    return "\n".join(out)


# The GitHub buttons when releases exist, else a "coming soon" note.
def _actions(info, where):
    if info["available"]:
        tag = (" (%s)" % _e(info["tag"])) if info.get("tag") else ""
        return ('<div class="bc-host-actions"><a class="bc-host-primary" href="%s" target="_blank" rel="noopener">Get the server on GitHub%s%s</a>'
                '<a class="bc-host-secondary" href="%s" target="_blank" rel="noopener">All releases</a>%s</div>'
                % (_e(info["latest"]), tag, _EXT_ICON, _e(info["all"]), '' if where == "page" else '<a class="bc-host-secondary" href="/host">Setup steps</a>'))
    return ('<div class="bc-host-actions"><span class="bc-host-primary is-soon" aria-disabled="true">Server downloads coming soon</span>%s</div>'
            '<p class="bc-host-note">The server code is getting ready for its first public release on GitHub. This page switches over by itself once it\'s published.</p>'
            % ('' if where == "page" else '<a class="bc-host-secondary" href="/host">Setup steps</a>'))


def host_dl_snippet():
    """Under the app downloads on the sign-in page: a link that opens the server pop-up (falls back to /host)."""
    info = host_info()
    return ('<p class="bc-host-more">Want to host your own server? <a href="/host" id="bc-host-open" aria-haspopup="dialog" aria-controls="bc-host-dialog">See server downloads</a></p>\n'
            '<dialog id="bc-host-dialog" class="bc-host-dialog" aria-labelledby="bc-host-title" aria-describedby="bc-host-lead">'
            '<div class="bc-host-dhead"><h2 id="bc-host-title">Host your own <span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span></h2>'
            '<button type="button" class="bc-host-x" id="bc-host-close" aria-label="Close"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>'
            '<div class="bc-host-dbody"><p id="bc-host-lead" class="bc-host-lead">Run BranchChat on your own computer for your family, church or company. Pick where it will run:</p>'
            '<ul class="bc-host-plats">%s</ul>%s</div></dialog>' % (_platform_lines(True), _actions(info, "dialog")))


# The full /host page: platform cards with steps and requirements.
def host_page():
    info = host_info()
    cards = []
    for p in HOST_PLATFORMS:
        steps = "".join("<li>%s</li>" % _e(s) for s in p["steps"])
        cards.append('<section class="bc-host-card%s" id="%s"><h2>%s%s</h2><p>%s</p>%s<p class="bc-host-req"><b>Needs:</b> %s</p>%s</section>'
                     % ("" if p["ready"] else " is-soon", p["id"], _e(p["title"]), "" if p["ready"] else ' <span class="bc-host-soon">Coming soon</span>',
                        _e(p["line"]), ('<pre><code>%s</code></pre>' % _e(p["cmd"])) if p["cmd"] else "", _e(p["req"]),
                        ("<ol>%s</ol>" % steps) if steps else ""))
    docs = ('<a href="%s" target="_blank" rel="noopener">full self-hosting guide</a>' % _e(SERVER_DOCS_URL)) if SERVER_DOCS_URL else \
        "full self-hosting guide (docs/self-hosting.md in the download)"
    return """<!doctype html>
<html lang="en" %s>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Host your own BranchChat</title>
<meta name="description" content="Run your own BranchChat server on Linux, a Raspberry Pi or Proxmox.">
<link rel="icon" href="/favicon.png">
<script src="/static/scheme.js?v=2"></script>
<link rel="stylesheet" href="/static/host.css?v=%s">
</head>
<body class="bc-host-page">
<main class="bc-host-main">
<p class="bc-host-back"><a href="/">&larr; Back to BranchChat</a></p>
<h1>Host your own <span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span></h1>
<p class="bc-host-lead">Run the BranchChat server on your own hardware: your messages and photos stay on a computer you control, still end-to-end encrypted. Downloads, source and dev builds are on GitHub.</p>
%s
<div class="bc-host-grid">
%s
</div>
<p class="bc-host-foot">Every download has setup steps and the %s. Questions about licensing for organizations? See About on any BranchChat server.</p>
</main>
<!-- 26.10.2 (Oct 18 2026): like every other page, end with just the tiny trademark line (full footer is on About only). -->
<footer id="bc-footer" class="bc-mini host-mini"><p class="legal">&copy; 2026 BranchChat. BranchChat&trade; is a trademark of BranchChat. <a class="bc-license-link" href="/license">License</a></p></footer>
</body>
</html>
""" % (host_attrs(), ASSET_V, _actions(info, "page"), "\n".join(cards), docs)


if HOST_LINK:
    try:
        import downloads as _downloads
        _downloads.EXTRA_HTML = host_dl_snippet
    except Exception:
        pass
