#!/usr/bin/env python3
"""Branch Chat Server

The browser encrypts messages and files before they arrive here.
This process stores ciphertext, account names, friends, lobby
membership, timestamps, and reaction emoji. It cannot read a message or a file.

Deleting a message erases that ciphertext and file, and leaves a tombstone
so other clients can show that the message was removed. Editing replaces
ciphertext in place and stamps edited_seq, because seq does not move.
Profile pictures are account pictures, not message content, so they are
stored as JPEG the server can see. They are shown only to the signed-in account.

Restart this service after a Python change. HTML, CSS, and JS are read
from disk and do not need a restart.
"""
import base64
import gzip
import heapq
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import zlib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

import bcconfig
import accounts
import downloads  # download links on the sign-in page; no-op unless BC_DOWNLOAD_LINKS=1
import legalpage  # licensing: /license, /license/terms, /license/third-party
import featuremail  # 26.10.4: "What's new" email for brand-new accounts (off unless BC_NEWUSER_FEATURE_EMAIL=1)
import publicmode  # public-instance features; every hook is a no-op unless PUBLIC_MODE / BC_* flags are set
import directory
import frontdoor  # one-screen sign-up / log-in front door (public home only); no-op unless BC_FRONTDOOR
import adminpage   # 0.23.3-beta.15: Administrator page (settings file editor + admin tools)
import unfurl
import webpush
import bugreports  # 26.10.5: in-app "Report a bug" form (bug-reports/YYYY-MM-DD.jsonl)
import welcomebot  # 26.10.3 BranchChat Helper bot (off unless BC_WELCOME_BOT=1)

# 0.19 share target: same cap as a chat message.
SHARE_MAX_CHARS = 2000
SHARE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# Paths, address and names come from bcconfig (env vars or /etc/branch-chat/config.env).
# Every setting has a safe default; see docs/self-hosting.md.
HERE = bcconfig.APP_DIR
STATE_DIR = bcconfig.STATE_DIR
BLOB_DIR = os.path.join(STATE_DIR, "blobs")
CHUNK_DIR = os.path.join(STATE_DIR, "upload-chunks")
MESSAGE_PATH = os.path.join(STATE_DIR, "messages.jsonl")
SOCIAL_PATH = os.path.join(STATE_DIR, "social.json")
LOBBY_PATH = os.path.join(STATE_DIR, "lobby.json")
DEVICES_PATH = os.path.join(STATE_DIR, "devices.json")
REACT_PATH = os.path.join(STATE_DIR, "reactions.json")
KEY_BACKUP_PATH = os.path.join(STATE_DIR, "key-backup.json")
KLIPY_KEY_PATH = os.path.join(STATE_DIR, "klipy.key")
AVATAR_DIR = os.path.join(STATE_DIR, "avatars")
AVATAR_MAX = 150 * 1024

HOSTS = set(bcconfig.HOSTS)
HOST = bcconfig.HOST
PORT = bcconfig.PORT
PUBLIC_URL = bcconfig.PUBLIC_URL
SCHEMA_VERSION = 1
BC_VERSION_LABEL = bcconfig.VERSION  # the one VERSION line (see stamp_version below)

# 26.10.8: ONE version source. The only place the number is written is the repo-root VERSION file (one line), read by
# bcconfig.VERSION (BC_VERSION env, else web/VERSION written from it by the Docker build / deploy, else ../VERSION).
# Pages and the service worker carry placeholders that are filled in here as they are served:
#   {{BC_VERSION}} -> 26.10.8 (labels, ?v= cache-busters)   {{BC_VERSION_SPOKEN}} -> "26.11.0 beta 2" style (aria-labels)
#   {{BC_SHELL}}   -> 26.10.8-<fingerprint of the served pages and app code>, the service worker's cache name
def _version_fingerprint():
    h = 0
    for name in sorted(os.listdir(HERE)):
        if name.endswith((".html", ".webmanifest")) or name == "sw.js":
            try:
                with open(os.path.join(HERE, name), "rb") as fh: h = zlib.crc32(fh.read(), h)
            except OSError: pass
    # 26.10.10c: every top-level static .js/.css, so any edit changes the app's ?v= asset stamp too.
    sd = os.path.join(HERE, "static")
    for name in sorted(os.listdir(sd)) if os.path.isdir(sd) else ():
        if name.endswith((".js", ".css")):
            try:
                with open(os.path.join(sd, name), "rb") as fh: h = zlib.crc32(fh.read(), h)
            except OSError: pass
    return "%08x" % h
_V_CORE, _, _V_PRE = bcconfig.VERSION.partition("-")
_VERSION_SUBS = ((b"{{BC_VERSION_SPOKEN}}", (_V_CORE + ((" " + _V_PRE.replace(".", " ")) if _V_PRE else "")).encode()),
                 (b"{{BC_VERSION}}", bcconfig.VERSION.encode()),
                 (b"{{BC_SHELL}}", (bcconfig.VERSION + "-" + _version_fingerprint()).encode()),
                 # 26.10.10c: index.html's ?v= asset stamp. Version + code fingerprint, so a redeploy of the same version
                 # with changed code gets new URLs (the version-only ?v= was immutable for a year at browsers and the CDN).
                 (b"{{BC_ASSET}}", (bcconfig.VERSION + "-" + _version_fingerprint()).encode()))
BC_ASSET_STAMP = bcconfig.VERSION + "-" + _version_fingerprint()
def stamp_version(body):
    if b"{{BC_" not in body:
        return body
    for k, v in _VERSION_SUBS:
        body = body.replace(k, v)
    return body
# 26.10.2: BC_GROUPS=0 turns group chats off on this server (default on). Creating groups, adding members and group invite
# links are refused, and the app hides New group / Start a group. Existing groups keep working (leave, keys, messages).
GROUPS_ENABLED = bcconfig.get("BC_GROUPS", "1").strip().lower() not in ("0", "false", "no", "off")
_GROUPS_OFF_PATHS = ("/api/groups/create", "/api/groups/add", "/api/glinks/create", "/api/glinks/peek", "/api/glinks/redeem")
SCHEMA_PATH = os.path.join(STATE_DIR, "SCHEMA_VERSION")
PLAIN_MAX = 256 * 1024 * 1024
CHUNK = 8 * 1024 * 1024
PART_MAX = CHUNK + 64 * 1024
JSON_MAX = 96 * 1024
STORE_BUDGET = 10 * 1024 * 1024 * 1024
FREE_FLOOR = 1024 * 1024 * 1024
GIF_MAX = 8 * 1024 * 1024
TEXT_CT_MAX = 48000
SYNC_LIMIT = 100
REACT_CAP = 5

HEX32 = re.compile(r"^[0-9a-f]{32}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
KLIPY_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
KLIPY_RENDITIONS = ("sm", "md", "xs", "hd")

CSP = (
# 26.10.5: connect-src also allows blob: and data: so the Android app can read back a received picture or file
# it is saving to the phone (the app's save hook reads the page's own in-memory copy; nothing leaves the device).
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; img-src 'self' blob: data: https://i.ytimg.com; frame-src https://www.youtube-nocookie.com https://www.youtube.com https://ko-fi.com; media-src 'self' blob:; "
    "connect-src 'self' blob: data:; worker-src 'self'; font-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

_store_lock = threading.Lock()
# 26.10.8 perf: response compression. Brotli is optional (pip install brotli); gzip is always available.
try:
    import brotli as _brotli
except Exception:
    _brotli = None
COMPRESS_MIN = 1024
COMPRESSIBLE = {"text/html", "text/css", "text/javascript", "application/javascript", "application/json",
                "image/svg+xml", "application/manifest+json", "text/plain", "application/wasm"}
_compress_cache = {}
_compress_lock = threading.Lock()
_klipy_lock = threading.Lock()
_klipy_hits = {}
_klipy_seen = {}
_klipy_blobs = {}
_last_post = {}
_reset_sent = []
_messages = []
_blob_seq = {}


def b64_ok(value, lo, hi):
    """True when a string is standard base64 inside the length bounds the caller expects."""
    return isinstance(value, str) and lo <= len(value) <= hi and bool(B64_RE.match(value))


# ---- 26.10.8 perf: change notifications for long-poll (/api/wait) ----
# Any change a sync could show (new/edited/deleted message, reaction, typing, presence, friends, devices, groups,
# settings) bumps _change_ver and wakes every waiting /api/wait request; the browser then runs a normal /api/sync.
_change_cond = threading.Condition()
_change_ver = 0
WAIT_MAX = 25
SYNC_DELTA_KEYS = ("online", "friends", "incoming", "outgoing", "devices", "lobby", "groups", "mutes", "chatlist",
                   "display_names", "name_colors", "contacts", "nicknames", "avatars")


def bump_change():
    global _change_ver
    with _change_cond:
        _change_ver += 1
        _change_cond.notify_all()


def atomic_write(path, text):
    """Replace a root-only state file. A crash leaves the previous file in place."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    bump_change()


def load_key_store():
    """Backups, email codes, reset codes, and finish nonces share one mode-600 file."""
    data = read_json(KEY_BACKUP_PATH, {})
    if not isinstance(data, dict):
        data = {}
    out = {}
    for name in ("backups", "codes", "finish", "factors", "resets"):
        bucket = data.get(name)
        out[name] = bucket if isinstance(bucket, dict) else {}
    return out


def save_key_store(store):
    """Write the key store back. Callers hold the store lock when they change it."""
    atomic_write(KEY_BACKUP_PATH, json.dumps(store, separators=(",", ":")))


def key_backup_exists(user_key):
    """True when this account has a password-sealed device backup. The server cannot open it."""
    backup = load_key_store()["backups"].get(user_key)
    return isinstance(backup, dict) and bool(backup.get("ct"))


def delete_key_backup(user_key):
    """Remove the backup and unused codes for this account. Password reset and account delete use this."""
    store = load_key_store()
    for name in ("backups", "codes", "finish", "factors", "resets"):
        store[name].pop(user_key, None)
    save_key_store(store)


def avatar_version(user_key):
    """fix-avatar-refresh: a picture's version is its file's mtime in ms (0 = none). It goes into /api/sync so every
    open page learns about a new picture on the existing /api/wait wake-up and loads /api/avatar?u=..&v=<ver>."""
    path = avatar_path(user_key)
    try:
        return int(os.stat(path).st_mtime_ns // 1000000) if path else 0
    except OSError:
        return 0


def avatars_for(names):
    """Map visible usernames to their picture version. People without a picture are left out."""
    out = {}
    for n in names:
        if isinstance(n, str) and n:
            u = accounts.find_user(n)
            ver = avatar_version(u["username_key"]) if u else 0
            if ver:
                out[n] = ver
    return out


def avatar_name_ok(user_key):
    """Avatar files are named from the account key. Reject anything that could leave that directory."""
    return isinstance(user_key, str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", user_key))


def avatar_path(user_key):
    """Path for this account's JPEG. Empty when the key is not a safe filename."""
    if not avatar_name_ok(user_key):
        return ""
    return os.path.join(AVATAR_DIR, user_key + ".jpg")


DISPLAY_PATH = os.path.join(STATE_DIR, "display_names.json")
DISPLAY_MAX = 32


def clean_display(value):
    """Trim a display name, drop control characters, and cap it at 32 characters."""
    if not isinstance(value, str):
        return None
    value = "".join(ch for ch in value if ch.isprintable())
    value = " ".join(value.split())
    if len(value) > DISPLAY_MAX:
        return None
    return value


# Everyone's chosen display names ({account key: name}); a missing file reads as empty.
def load_display_names():
    return read_json(DISPLAY_PATH, {})


def set_display_name(user_key, value):
    """Store or clear a display name. The username stays the login."""
    with _store_lock:
        store = load_display_names()
        if value:
            store[user_key] = value
        else:
            store.pop(user_key, None)
        atomic_write(DISPLAY_PATH, json.dumps(store))


def account_bar_name(user):
    """26.10.2: the name for the bottom-left account bar: the display name if one is set, otherwise the username
    exactly as it was typed at sign-up (e.g. "BoogieMan", not the lower-case login key)."""
    if not user:
        return ""
    value = load_display_names().get(user.get("username_key") or "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return user.get("username") or user.get("username_key") or ""


def display_names_for(names):
    """Map visible usernames to their display names. Names without one are left out."""
    wanted = {n for n in names if isinstance(n, str) and n}
    out = {}
    for key, value in load_display_names().items():
        if isinstance(value, str) and value:
            name = name_of(key)
            if name in wanted:
                out[name] = value
    return out


# 26.10.5: nicknames. Each person can give any of their friends a private nickname that only they see
# ({viewer account key: {friend account key: nickname}}). Same 32-character rule as display names.
NICK_PATH = os.path.join(STATE_DIR, "nicknames.json")


def load_nicknames():
    return read_json(NICK_PATH, {})


def set_nickname(viewer_key, friend_key, value):
    """Store or clear (empty value) one nickname for one viewer."""
    with _store_lock:
        store = load_nicknames()
        row = store.get(viewer_key)
        row = row if isinstance(row, dict) else {}
        if value:
            row[friend_key] = value
        else:
            row.pop(friend_key, None)
        if row:
            store[viewer_key] = row
        else:
            store.pop(viewer_key, None)
        atomic_write(NICK_PATH, json.dumps(store))


def nicknames_for(viewer_key, social):
    """This viewer's nicknames for people who are still their friend, keyed by username. Never anyone else's."""
    row = load_nicknames().get(viewer_key)
    if not isinstance(row, dict):
        return {}
    friends = set(friends_of(viewer_key, social))
    return {name_of(k): v for k, v in row.items() if k in friends and isinstance(v, str) and v}


COLOR_PATH = os.path.join(STATE_DIR, "name_colors.json")
NAME_PALETTE = ['#f2c94c', '#f2994a', '#ff7a7a', '#ff6fb5', '#d68cff', '#a78bfa', '#7aa2ff', '#56ccf2', '#4dd9c6', '#6fcf97', '#a6e35f', '#e0c097', '#c9d1d9', '#ffd6a5']
HEX_COLOR = re.compile(r"#[0-9a-f]{6}")


def clean_color(value):
    """Empty means default. Otherwise a palette color or a strict #rrggbb hex."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value:
        return ""
    if value in NAME_PALETTE or HEX_COLOR.fullmatch(value):
        return value
    return None


# Saved name colours ({account key: '#rrggbb'}) people picked for themselves.
def load_name_colors():
    return read_json(COLOR_PATH, {})


# Save or clear one person's name colour (empty value clears it).
def set_name_color(user_key, value):
    with _store_lock:
        store = load_name_colors()
        if value:
            store[user_key] = value
        else:
            store.pop(user_key, None)
        atomic_write(COLOR_PATH, json.dumps(store))


# Name colours for just the usernames asked about, keyed by username for the browser.
def name_colors_for(names):
    wanted = {n for n in names if isinstance(n, str) and n}
    out = {}
    for key, value in load_name_colors().items():
        if isinstance(value, str) and HEX_COLOR.fullmatch(value):
            name = name_of(key)
            if name in wanted:
                out[name] = value
    return out


CONTACT_PATH = os.path.join(STATE_DIR, "contact_prefs.json")
PHONE_RE = re.compile(r"[0-9 +().-]{7,32}")


def clean_phone(value):
    """Empty clears. Otherwise digits, spaces, + - ( ) . with 7 to 20 digits."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return ""
    if not PHONE_RE.fullmatch(value):
        return None
    digits = sum(ch.isdigit() for ch in value)
    return value if 7 <= digits <= 20 else None


# Contact details people chose to share (email shown or not, phone, phone shown or not).
def load_contacts():
    return read_json(CONTACT_PATH, {})


# One person's contact sharing settings, with safe defaults when they never set any.
def contact_of(user_key):
    row = load_contacts().get(user_key)
    row = row if isinstance(row, dict) else {}
    return {"show_email": bool(row.get("show_email")), "phone": row.get("phone") if isinstance(row.get("phone"), str) else "", "show_phone": bool(row.get("show_phone"))}


# Save one person's contact sharing; an all-off row is removed instead of stored.
def set_contact(user_key, row):
    with _store_lock:
        store = load_contacts()
        if row and (row.get("show_email") or row.get("phone") or row.get("show_phone")):
            store[user_key] = row
        else:
            store.pop(user_key, None)
        atomic_write(CONTACT_PATH, json.dumps(store))


def contacts_for(names):
    """Only opted-in email and phone for visible users. Nobody else's contact info leaves the server."""
    wanted = {n for n in names if isinstance(n, str) and n}
    out = {}
    for key, row in load_contacts().items():
        if not isinstance(row, dict):
            continue
        name = name_of(key)
        if name not in wanted:
            continue
        item = {}
        if row.get("show_email"):
            found = accounts.find_user(key)
            email = ((found or {}).get("email") or "").strip()
            if email:
                item["email"] = email
        if row.get("show_phone") and isinstance(row.get("phone"), str) and row.get("phone"):
            item["phone"] = row["phone"]
        if item:
            out[name] = item
    return out


PREFS_PATH = os.path.join(STATE_DIR, "prefs.json")
TONES = ("chime", "bell", "pop", "marimba", "ping", "off")
# 26.10.3: sound settings saved per account. CLICKS = the button sound sets (made in the browser with Web Audio, no
# sound files); vol_ui / vol_char = Button sounds and Character sounds volume, 0-100 (missing = 100, today's loudness).
CLICKS = ("classic", "soft", "wood", "bubble", "chime", "click")
SOUND_VOL_KEYS = ("vol_ui", "vol_char")


def sound_prefs_of(row):
    """The sound fields of one account's saved preferences, with defaults filled in."""
    out = {"click": row.get("click") if row.get("click") in CLICKS else "classic"}
    for k in SOUND_VOL_KEYS:
        v = row.get(k)
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 else 100
    return out


# Per-account preferences (for example the mature language filter); missing file = no preferences.
def load_prefs():
    return read_json(PREFS_PATH, {})


# 26.10.4 stickers: each account's recently sent stickers, newest first, as "pack/sticker" names, e.g. "friends/hi".
# Only names are kept (the pictures are this server's own static files), so nothing about a chat is stored here.
STICKER_RECENT_MAX = 24
STICKER_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}/[a-z0-9][a-z0-9-]{0,47}$")


def sticker_recent_clean(value):
    out = []
    if not isinstance(value, list):
        return out
    for item in value[:100]:
        if isinstance(item, str) and STICKER_NAME.match(item) and item not in out:
            out.append(item)
        if len(out) >= STICKER_RECENT_MAX:
            break
    return out


# 26.10.2: delete animation per account. A pick is a short lower-case name ("alt" = Take turns, "fisherman", ...);
# the app decides which names it knows, the server only refuses anything that is not a plain short word.
SNAP_PICK_RE = re.compile(r"^[a-z]{1,20}$")
SEND_ANIM_RE = re.compile(r"^(|alt|robot|crane|snail|beaver|turtle|sparkle)$")   # 26.10.11: Send animations
# The moment this server first ran with per-account delete animations, written once and never changed afterwards
# (so restarts and later updates keep the same moment). Accounts made before it may take over the delete animation
# their browser remembered from before; accounts made after it start on Take turns.
SNAP_SINCE_PATH = os.path.join(STATE_DIR, "snap_prefs_since")


def snap_prefs_since():
    try:
        with open(SNAP_SINCE_PATH) as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        pass
    now = int(time.time())
    try:
        atomic_write(SNAP_SINCE_PATH, str(now))
    except OSError:
        pass
    return now


# True when the account was made before per-account delete animations arrived on this server. Accounts with no
# recorded creation time (the first owner account, very old accounts) count as old.
def account_predates_snap_prefs(user_key):
    found = accounts.find_user(user_key) or {}
    made = found.get("created") or ""
    if not made:
        return True
    try:
        made_at = int(time.mktime(time.strptime(made, "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return True
    return made_at < snap_prefs_since()


# 26.10.10 "What's new" pop-up (static/whatsnew.js): when this server first ran each VERSION. Accounts made after the
# current version went live never get its pop-up (brand-new accounts are marked seen instead).
WHATSNEW_SINCE_PATH = os.path.join(STATE_DIR, "whatsnew_since.json")
WHATSNEW_VER_RE = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,4}){1,3}(-[0-9A-Za-z.]{1,16})?$")


def whatsnew_since(version):
    try:
        with open(WHATSNEW_SINCE_PATH) as fh:
            m = json.load(fh)
        m = m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        m = {}
    if isinstance(m.get(version), int):
        return m[version]
    m[version] = int(time.time())
    try:
        atomic_write(WHATSNEW_SINCE_PATH, json.dumps(m))
    except OSError:
        pass
    return m[version]


def account_is_new_for_whatsnew(user_key):
    made = (accounts.find_user(user_key) or {}).get("created") or ""
    if not made:
        return False
    try:
        made_at = int(time.mktime(time.strptime(made, "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return False
    return made_at >= whatsnew_since(bcconfig.VERSION)


# Forget one account's preferences and chat mutes (used when an account is deleted).
def drop_prefs(user_key):
    with _store_lock:
        store = load_prefs()
        if store.pop(user_key, None) is not None:
            atomic_write(PREFS_PATH, json.dumps(store))
    drop_mutes(user_key)
    drop_chatlist(user_key)


# 0.23.2: per-chat mute. One row per account: {chat key: until}. until is a unix time in seconds, or 0 for
# "until I turn it back on". Chat keys are the app's own: "lobby", "dm:<username, lower case>", "g:<group id>".
# A muted chat still counts unread messages; it only skips sounds, alerts and the phone push wake-up.
# Older servers never read chat_mutes.json, so rolling back to 0.23.0/0.23.1 still starts (mutes are just ignored).
MUTES_PATH = os.path.join(STATE_DIR, "chat_mutes.json")
MUTE_FOR = {"1h": 3600, "8h": 8 * 3600, "1w": 7 * 86400, "forever": 0}
MUTE_KEY_RE = re.compile(r"^(lobby|dm:[a-z0-9][a-z0-9._-]{0,63}|g:[0-9a-f]{32})$")
MUTE_MAX = 300


# Chat mutes for every account ({account key: {chat key: until}}).
def load_mutes():
    return read_json(MUTES_PATH, {})


# Tidy a chat key for mutes ("lobby", "dm:<name>" or "g:<id>"); anything else becomes "".
def norm_mute_key(key):
    if not isinstance(key, str):
        return ""
    key = key.strip()
    if key.startswith("dm:"):
        key = "dm:" + key[3:].lower()
    return key if MUTE_KEY_RE.match(key) else ""


def mutes_of(user_key, store=None, now=None):
    """This account's live mutes {chat key: until}; expired rows are left out."""
    now = int(now if now is not None else time.time())
    row = (store if store is not None else load_mutes()).get(user_key)
    out = {}
    if isinstance(row, dict):
        for key, until in row.items():
            if isinstance(key, str) and isinstance(until, int) and not isinstance(until, bool) and (until == 0 or until > now):
                out[key] = until
    return out


# True when this person muted this chat (the mute may be forever or until a time).
def chat_muted(user_key, chat_key, store):
    until = mutes_of(user_key, store).get(chat_key)
    return until is not None


# Forget all of one account's chat mutes (account deletion).
def drop_mutes(user_key):
    with _store_lock:
        store = load_mutes()
        if store.pop(user_key, None) is not None:
            atomic_write(MUTES_PATH, json.dumps(store))


# chatlist-pin-reorder: this account's chat list layout, the same on every device and every server (it is stored
# with the account's other state). One row per account: {"pins": [keys], "order": [keys], "archived": [keys]}.
# Keys are the mute keys ("lobby", "dm:<name, lower case>", "g:<id>"). pins = the pinned block, top to bottom;
# order = the user's dragged order of the other chats (chats not in it show above it, newest activity first);
# archived = chats hidden from the main list (still friends/members; new messages leave them archived).
# Older servers never read chat_list.json, so a rollback still starts (the layout is just ignored).
CHATLIST_PATH = os.path.join(STATE_DIR, "chat_list.json")
CHATLIST_MAX = 500


def _chatlist_clean(items):
    """Valid, unique chat keys in order (at most CHATLIST_MAX)."""
    out, seen = [], set()
    for k in items if isinstance(items, list) else []:
        k = norm_mute_key(k)
        if k and k not in seen:
            seen.add(k)
            out.append(k)
            if len(out) >= CHATLIST_MAX:
                break
    return out


def chatlist_of(user_key, store=None):
    """This account's chat list layout {pins, order, archived} (empty lists when never set)."""
    row = (store if store is not None else read_json(CHATLIST_PATH, {})).get(user_key)
    row = row if isinstance(row, dict) else {}
    return {"pins": _chatlist_clean(row.get("pins")), "order": _chatlist_clean(row.get("order")),
            "archived": _chatlist_clean(row.get("archived"))}


# Forget one account's chat list layout (account deletion).
def drop_chatlist(user_key):
    with _store_lock:
        store = read_json(CHATLIST_PATH, {})
        if store.pop(user_key, None) is not None:
            atomic_write(CHATLIST_PATH, json.dumps(store))


def push_chat_key(row, sender_name):
    """The chat key a push target sees for this message (their side of a DM is the sender)."""
    box = row.get("box")
    if box == "lobby":
        return "lobby"
    if box == "group" and row.get("g"):
        return "g:" + row.get("g")
    if box == "dm" and sender_name:
        return "dm:" + sender_name.lower()
    return ""


def drop_avatar(user_key):
    """Delete the profile picture file. Missing files are ignored."""
    path = avatar_path(user_key)
    if not path:
        return
    try:
        os.remove(path)
        bump_change()  # fix-avatar-refresh
    except OSError:
        pass


def jpeg_ok(raw):
    """Canvas exports a baseline JPEG. Some encoders leave a few bytes after the end marker."""
    return (
        isinstance(raw, bytes)
        and 32 <= len(raw) <= AVATAR_MAX
        and raw.startswith(b"\xff\xd8\xff")
        and b"\xff\xd9" in raw[-64:]
    )


# 26.10.2: synced delete animations. The person who deletes a message tells the server which
# delete animation they saw (a short name like "owl") and a number that seeds its random bits.
# The server keeps both on the tombstone and hands them to every other device in the "removed"
# list, so those devices can play the same animation. Both are optional: older apps leave them
# out, and older apps ignore them when they arrive.
DELETE_ANIM_RE = re.compile(r"^[a-z]{1,16}$")


def delete_anim_from(data):
    """The (name, seed) pair from a delete request, or ("", None) when absent or malformed."""
    name = data.get("anim") if isinstance(data.get("anim"), str) else ""
    name = name if DELETE_ANIM_RE.match(name) else ""
    seed = data.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        seed = None
    return name, seed


def removed_item(msg, dseq):
    """One entry of the sync "removed" list, with the delete animation when the deleter sent one."""
    item = {"id": msg["id"], "seq": dseq}
    if msg.get("del_anim"):
        item["anim"] = msg["del_anim"]
        if msg.get("del_seed") is not None:
            item["seed"] = msg["del_seed"]
    return item


def deleted_seq_of(msg):
    """Removal cursor on a tombstone. Zero when this row was never removed."""
    try:
        return int(msg.get("deleted_seq") or 0)
    except (TypeError, ValueError):
        return 0


def next_deleted_seq():
    """Monotonic marker that is not a message seq. Sync uses it as the gone cursor.
    26.10.8 perf: a running maximum (set at load) instead of a scan; every caller stores the value it gets."""
    global _max_dseq
    _max_dseq += 1
    _idx_bump()
    return _max_dseq


def edited_seq_of(msg):
    """Edit cursor. Zero when this row has never been edited. It is not a message seq."""
    try:
        return int(msg.get("edited_seq") or 0)
    except (TypeError, ValueError):
        return 0


def next_edited_seq():
    """Next edit cursor. Clients that already passed the message seq learn about the new ciphertext from this.
    26.10.8 perf: a running maximum, like next_deleted_seq."""
    global _max_eseq
    _max_eseq += 1
    _idx_bump()
    return _max_eseq


# ---- 26.10.8 perf: message indexes ----
# _messages stays the source of truth (seq order). Alongside it: id -> row, per-chat seq lists, and sorted lists of
# deleted / edited rows that sync reads with a binary search instead of scanning the whole history three times.
# The deleted/edited lists are rebuilt lazily (one scan) only after a delete or edit, never on an idle poll.
_by_id = {}
_by_chat = {}
_max_dseq = 0
_max_eseq = 0
_idx_ver = 0
_idx_built = -1
_del_idx = []    # (deleted_seq, id, row), sorted
_edit_idx = []   # (edited_seq, seq, row), sorted; live rows only
_line_of = {}    # seq -> serialized log line (rows changed in place are re-serialized)


def _idx_bump():
    global _idx_ver
    _idx_ver += 1


def chat_key_of(row):
    box = row.get("box")
    if box == "lobby":
        return "lobby"
    if box == "group":
        return "g:" + (row.get("g") or "")
    if box == "dm":
        return "dm:%s:%s" % (row.get("a"), row.get("b"))
    return ""


_dm_keys_of = {}  # account key -> set of its dm chat keys


def _index_row(row):
    _by_id[row.get("id")] = row
    ck = chat_key_of(row)
    _by_chat.setdefault(ck, []).append(row)
    if row.get("box") == "dm":
        for who in (row.get("a"), row.get("b")):
            if who:
                _dm_keys_of.setdefault(who, set()).add(ck)


def rows_after_for(viewer, lobby, since):
    """26.10.8 perf: rows with seq > since in the chats this viewer could see (their DMs, the lobby if a member,
    groups they are in), in seq order. Callers still run can_see on each row; this only skips other people's chats."""
    keys = set(_dm_keys_of.get(viewer, ()))
    if viewer in lobby.get("members", {}):
        keys.add("lobby")
    for gid, g in _groups.items():
        if viewer in (g.get("members") or {}):
            keys.add("g:" + gid)
    seqkey = lambda r: r["seq"]
    parts = []
    for k in keys:
        rows = _by_chat.get(k)
        if rows:
            i = _first_after(rows, since, seqkey)
            if i < len(rows):
                parts.append(rows[i:] if i else rows)
    if len(parts) == 1:
        return parts[0]
    return heapq.merge(*parts, key=seqkey)


def reindex_messages():
    """Rebuild every index from _messages (after a load or an outside rewrite)."""
    global _max_dseq, _max_eseq
    _by_id.clear()
    _by_chat.clear()
    _dm_keys_of.clear()
    _line_of.clear()
    top_d = top_e = 0
    for row in _messages:
        _index_row(row)
        top_d = max(top_d, deleted_seq_of(row))
        top_e = max(top_e, edited_seq_of(row))
    _max_dseq, _max_eseq = top_d, top_e
    _idx_bump()


def _ensure_change_idx():
    """Sorted deleted/edited lists, rebuilt only when a delete or edit happened since the last build."""
    global _idx_built, _del_idx, _edit_idx
    if _idx_built == _idx_ver:
        return
    dl, el = [], []
    for row in _messages:
        d = deleted_seq_of(row)
        if d and row.get("deleted"):
            dl.append((d, row.get("id") or "", row))
        else:
            e = edited_seq_of(row)
            if e:
                el.append((e, row["seq"], row))
    dl.sort(key=lambda t: (t[0], t[1]))
    el.sort(key=lambda t: (t[0], t[1]))
    _del_idx, _edit_idx, _idx_built = dl, el, _idx_ver


def _first_after(rows, value, key):
    """Index of the first item whose key(item) > value, in a list sorted by that key."""
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if key(rows[mid]) <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def message_by_id(mid):
    return _by_id.get(mid)


def message_by_seq(seq):
    i = _first_after(_messages, seq - 1, lambda r: r["seq"])
    return _messages[i] if i < len(_messages) and _messages[i]["seq"] == seq else None


# ---- 26.10.8 perf: message log writes outside the store lock (durability kept) ----
# Every change to the log is queued under _store_lock in order (append line, or a full rewrite snapshot). The request
# then leaves _store_lock and calls log_flush(), which writes the queue in order under _log_lock and fsyncs once for
# everything queued (group commit). A request still answers only after its change is on disk.
_log_lock = threading.Lock()
_log_queue = []      # (gen, kind, payload)
_log_gen = 0
_log_done = 0


def _line(row):
    line = _line_of.get(row["seq"])
    if line is None:
        line = json.dumps(row, separators=(",", ":")) + "\n"
        _line_of[row["seq"]] = line
    return line


def log_queue_append(row):
    global _log_gen
    _log_gen += 1
    _log_queue.append((_log_gen, "a", _line(row)))
    bump_change()
    return _log_gen


def log_queue_rewrite(*changed):
    """Queue a full rewrite. changed: rows edited in place (None = re-serialize every row)."""
    global _log_gen
    if changed and changed[0] is None:
        _line_of.clear()
    for row in changed:
        if row is not None:
            _line_of.pop(row.get("seq"), None)
    _log_gen += 1
    _log_queue.append((_log_gen, "r", [_line(row) for row in _messages]))
    bump_change()
    return _log_gen


def log_flush(upto=None):
    """Write queued log changes up to gen `upto` (default: everything queued so far), in order, one fsync.
    Takes only _log_lock (never _store_lock), so it is safe to call with or without _store_lock held."""
    global _log_done
    if upto is None:
        upto = _log_gen
    if _log_done >= upto:
        return
    with _log_lock:
        if _log_done >= upto:
            return
        n = len(_log_queue)
        ops = _log_queue[:n]
        del _log_queue[:n]    # items queued meanwhile stay for the next flush
        if not ops:
            return
        last_r = max((i for i, op in enumerate(ops) if op[1] == "r"), default=-1)
        os.makedirs(STATE_DIR, exist_ok=True)
        if last_r >= 0:
            tmp = MESSAGE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("".join(ops[last_r][2]))
                for op in ops[last_r + 1:]:
                    fh.write(op[2])
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, MESSAGE_PATH)
        else:
            with open(MESSAGE_PATH, "a", encoding="utf-8") as fh:
                fh.write("".join(op[2] for op in ops))
                fh.flush()
                os.fsync(fh.fileno())
        os.chmod(MESSAGE_PATH, 0o600)
        _log_done = ops[-1][0]


_tl = threading.local()   # .pending: newest log gen this request queued; flushed before its response is sent


def _note_pending(gen):
    _tl.pending = max(getattr(_tl, "pending", 0), gen)


def flush_pending():
    pend = getattr(_tl, "pending", 0)
    if pend:
        _tl.pending = 0
        log_flush(pend)


def _log_flusher():
    """Safety net for changes queued outside a request (timers, the Helper bot): on disk within ~0.2 s."""
    while True:
        time.sleep(0.2)
        try:
            if _log_done < _log_gen:
                log_flush()
        except Exception as exc:
            print("log flush failed:", exc, file=sys.stderr, flush=True)


def rewrite_messages_now():
    """For other modules that change rows in place (welcome bot, account wipe): full rewrite, written before return."""
    gen = log_queue_rewrite(None)
    _idx_bump()
    log_flush(gen)



def same_chat(a, b):
    """True when both rows are the lobby, or both are the same direct-message pair."""
    if a.get("box") != b.get("box"):
        return False
    if a.get("box") == "lobby":
        return True
    if a.get("box") == "group":
        return bool(a.get("g")) and a.get("g") == b.get("g")
    if a.get("box") != "dm":
        return False
    return a.get("a") == b.get("a") and a.get("b") == b.get("b")


def sender_may_edit(msg, user_key):
    """True when this is one of the sender's three newest live messages in that chat."""
    if msg.get("deleted") or msg.get("sender") != user_key:
        return False
    newer = 0
    mine = int(msg.get("seq") or 0)
    for item in _by_chat.get(chat_key_of(msg), _messages):
        if item.get("deleted") or item.get("sender") != user_key or not same_chat(item, msg):
            continue
        if int(item.get("seq") or 0) > mine:
            newer += 1
            if newer >= 3:
                return False
    return True


def read_json(path, fallback):
    """Load JSON, or the fallback when the file is missing or the top-level type is wrong."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def load_messages():
    """Read the append-only message log into memory and index file ids by sequence."""
    global _messages, _blob_seq
    rows = []
    index = {}
    try:
        fh = open(MESSAGE_PATH, encoding="utf-8")
    except OSError:
        _messages = []
        _blob_seq = {}
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("seq"), int):
                rows.append(row)
                blob = row.get("blob") if isinstance(row.get("blob"), dict) else None
                if blob and HEX32.match(blob.get("id") or ""):
                    index[blob["id"]] = row["seq"]
    rows.sort(key=lambda r: r["seq"])   # 26.10.8: appends are written in seq order; sorting keeps old files safe too
    _messages = rows
    _blob_seq = index
    reindex_messages()


def append_message(row):
    """Add one row. Sequence numbers are assigned by the caller (under _store_lock).
    26.10.8 perf: the line is queued in order here; the caller calls log_flush() after leaving _store_lock, which
    writes and fsyncs before the request answers (send_json/send_bytes call flush_pending first)."""
    _messages.append(row)
    _index_row(row)
    blob = row.get("blob")
    if isinstance(blob, dict) and blob.get("id"):
        _blob_seq[blob["id"]] = row["seq"]
    gen = log_queue_append(row)
    _note_pending(gen)
    return gen


_ro_cache = {}


def _read_json_ro(path, fallback):
    """26.10.8 perf: parsed JSON kept in memory and reused while the file is unchanged (same inode, size and mtime;
    atomic_write always makes a new inode). READ-ONLY: callers must not change the returned object."""
    try:
        st = os.stat(path)
        sig = (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return fallback
    hit = _ro_cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    data = read_json(path, fallback)
    _ro_cache[path] = (sig, data)
    return data


def load_social_ro():
    data = _read_json_ro(SOCIAL_PATH, {})
    pairs = data.get("pairs") if isinstance(data, dict) else None
    requests = data.get("requests") if isinstance(data, dict) else None
    if isinstance(pairs, list) and isinstance(requests, list):
        return data
    return load_social()


def load_devices_ro():
    data = _read_json_ro(DEVICES_PATH, {})
    rows = data.get("devices") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def load_lobby_ro():
    data = _read_json_ro(LOBBY_PATH, {})
    if isinstance(data, dict) and isinstance(data.get("members"), dict) and isinstance(data.get("epochs"), dict) and "epoch" in data:
        return data
    return load_lobby()


def load_social():
    """Friend pairs and pending requests. A bad file becomes an empty list, not a crash."""
    data = read_json(SOCIAL_PATH, {})
    pairs = data.get("pairs") if isinstance(data, dict) else None
    requests = data.get("requests") if isinstance(data, dict) else None
    if not isinstance(pairs, list):
        pairs = []
    if not isinstance(requests, list):
        requests = []
    return {"pairs": pairs, "requests": requests}


def save_social(data):
    """Write the friend graph."""
    atomic_write(SOCIAL_PATH, json.dumps(data))


def load_devices():
    """Public keys for each signed-in device. Private keys never appear here."""
    data = read_json(DEVICES_PATH, {})
    rows = data.get("devices") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def save_devices(rows):
    """Write the device list."""
    atomic_write(DEVICES_PATH, json.dumps({"devices": rows}))


def load_lobby():
    """Lobby membership and epoch wraps. Missing fields are filled so later code can index them."""
    data = read_json(LOBBY_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("epoch", 0)
    data.setdefault("members", {})
    data.setdefault("epochs", {})
    if not isinstance(data["members"], dict):
        data["members"] = {}
    if not isinstance(data["epochs"], dict):
        data["epochs"] = {}
    return data


def save_lobby(data):
    """Write lobby membership. Joining or leaving rotates the epoch in the caller."""
    atomic_write(LOBBY_PATH, json.dumps(data))


# ---- 0.23.0 group chats ----
# One file per group in STATE_DIR/groups/<id>.json. Like the lobby, a group has an epoch key that is wrapped to each
# member device. The member who adds or removes someone makes the next key in the same request. Leaving makes an
# empty epoch that the next online member fills (the person leaving must not know it). Message rows use box "group"
# and the field "g". Server-written notices (create/add/remove/leave/owner) are rows with "event" and no ciphertext.
# Older servers ignore all of this: can_see() returns False for an unknown box, so a rollback still starts.
GROUP_DIR = os.path.join(STATE_DIR, "groups")
GROUP_MAX = 20
GROUP_PER_USER = 30
GROUP_WRAP_MAX = 8 * GROUP_MAX
GROUP_META_MAX = 4000
GROUP_BATCH_MAX = 200
GROUP_REKEY = "The group is getting a new key. Wait a moment and try again."
GROUP_ROLE_RANK = {"owner": 0, "admin": 1, "member": 2}
_groups = {}


def load_groups():
    """Read every group file into memory. A broken file is skipped, not fatal."""
    global _groups
    out = {}
    try:
        names = os.listdir(GROUP_DIR)
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json") or not HEX32.match(name[:-5]):
            continue
        data = read_json(os.path.join(GROUP_DIR, name), {})
        if not isinstance(data, dict) or data.get("id") != name[:-5]:
            continue
        for field, empty in (("members", {}), ("epochs", {}), ("meta", {}), ("settings", {})):
            if not isinstance(data.get(field), dict):
                data[field] = empty
        try:
            data["epoch"] = max(1, int(data.get("epoch") or 1))
        except (TypeError, ValueError):
            data["epoch"] = 1
        out[data["id"]] = data
    _groups = out


def save_group(g):
    """Write one group file (mode 600 in a 700 directory)."""
    os.makedirs(GROUP_DIR, exist_ok=True)
    os.chmod(GROUP_DIR, 0o700)
    atomic_write(os.path.join(GROUP_DIR, g["id"] + ".json"), json.dumps(g, separators=(",", ":")))


def live_group(gid):
    """The group with this id, or None when it is unknown or deleted."""
    g = _groups.get((gid or "").lower()) if isinstance(gid, str) else None
    if not g or g.get("deleted"):
        return None
    return g


def groups_of(key):
    """Live groups this account belongs to."""
    return [g for g in _groups.values() if not g.get("deleted") and key in g["members"]]


_group_acts = {}


def group_too_fast(key):
    """One group change per second per account. Separate from too_fast so a message right after a change is not refused."""
    now = time.time()
    if now - _group_acts.get(key, 0) < 1:
        return True
    _group_acts[key] = now
    return False


# This account's role in a group (owner, admin or member), or "" when not a member.
def group_role(g, key):
    row = g["members"].get(key) if g else None
    return (row.get("role") or "member") if isinstance(row, dict) else ""


def group_event(g, kind, actor, target="", extra=None):
    """Append a plaintext notice row at the group's current epoch. It has no sender, so it never counts as a message.
    0.23.4: a "rename" notice also carries the new name sealed with the group key (name_iv/name_ct); the server can't read it."""
    row = {
        "box": "group", "g": g["id"], "a": "", "b": "", "epoch": int(g["epoch"]),
        "event": kind, "actor": actor or "", "target": target or "",
        "seq": (_messages[-1]["seq"] + 1) if _messages else 1,
        "id": secrets.token_hex(16), "sender": "", "sender_name": "", "ts": int(time.time()),
    }
    if extra:
        row.update(extra)
    append_message(row)
    return row


def group_drop_wraps(g, key):
    """Forget every stored wrap for one account (after it leaves or is removed)."""
    for number, rows in list(g["epochs"].items()):
        if isinstance(rows, list):
            g["epochs"][number] = [w for w in rows if isinstance(w, dict) and w.get("user") != key]


def group_heir(g):
    """Longest-serving admin, else longest-serving member."""
    rows = [(k, r) for k, r in g["members"].items() if isinstance(r, dict)]
    for wanted in ("admin", "member"):
        pick = sorted([(int(r.get("since") or 0), k) for k, r in rows if (r.get("role") or "member") == wanted])
        if pick:
            return pick[0][1]
    return ""


def group_leave(g, key):
    """Take one account out. The epoch moves on with no key yet; the next online member makes it."""
    row = g["members"].pop(key, None)
    if row is None:
        return
    group_drop_wraps(g, key)
    if not g["members"]:
        g["deleted"] = True
        g["deleted_ts"] = int(time.time())
        save_group(g)
        return
    g["epoch"] = int(g["epoch"]) + 1
    g["epochs"][str(g["epoch"])] = []
    group_event(g, "leave", key)
    if (row.get("role") or "") == "owner":
        heir = group_heir(g)
        if heir:
            g["members"][heir]["role"] = "owner"
            group_event(g, "owner", "", heir)
    save_group(g)


def group_view(g, viewer, devices, online, disabled):
    """What one member may know about a group: members, the encrypted name, this account's wraps, and courier work."""
    me = g["members"].get(viewer) or {}
    epoch = int(g["epoch"])
    start = int(me.get("epoch_from") or 1)
    members = []
    for key, row in g["members"].items():
        if not isinstance(row, dict):
            continue
        name = row.get("name") or name_of(key)
        item = {"username": name, "role": row.get("role") or "member", "online": name in online, "since": int(row.get("since") or 0)}
        if key in disabled:
            item["online"] = False
            item["hidden"] = True
        members.append(item)
    members.sort(key=lambda i: (GROUP_ROLE_RANK.get(i["role"], 3), i["username"].lower()))
    my_ids = {row.get("device_id") for row in devices if row.get("user") == viewer}
    wraps = []
    held = set()
    for number, rows in g["epochs"].items():
        try:
            n = int(number)
        except ValueError:
            continue
        if n < start or not isinstance(rows, list):
            continue
        for w in rows:
            if isinstance(w, dict) and w.get("user") == viewer and w.get("device_id") in my_ids:
                wraps.append({"epoch": n, "device_id": w.get("device_id") or "", "eph_pub": w.get("eph_pub") or "",
                              "iv": w.get("iv") or "", "ct": w.get("ct") or ""})
                held.add(n)
    current = g["epochs"].get(str(epoch))
    missing = []
    if held:
        for key, row in g["members"].items():
            if not isinstance(row, dict) or len(missing) >= GROUP_BATCH_MAX:
                continue
            mstart = int(row.get("epoch_from") or 1)
            mdevs = [d for d in devices if d.get("user") == key]
            for n in sorted(held):
                if n < mstart:
                    continue
                rows = g["epochs"].get(str(n)) or []
                covered = {w.get("device_id") for w in rows if isinstance(w, dict) and w.get("user") == key}
                for d in mdevs:
                    if d.get("device_id") not in covered and len(missing) < GROUP_BATCH_MAX:
                        missing.append({"epoch": n, "username": row.get("name") or name_of(key),
                                        "device_id": d.get("device_id"), "public_key": d.get("public_key")})
    meta = g.get("meta") or {}
    can_invite = (me.get("role") or "member") == "owner"   # 26.10.13: owner-only
    return {
        "id": g["id"],
        "epoch": epoch,
        "epoch_from": start,
        "role": me.get("role") or "member",
        "history": g.get("history") or "join",
        "settings": dict(g.get("settings") or {}),
        "created": int(g.get("created") or 0),
        "members": members,
        "meta": {"epoch": int(meta.get("epoch") or 0), "iv": meta.get("iv") or "", "ct": meta.get("ct") or "", "rev": int(meta.get("rev") or 0)},
        "wraps": wraps,
        "needs_epoch": not (isinstance(current, list) and current),
        "missing": missing,
        # 0.23.1: who may share the group's invite link, and the active link (sealed; only its makers can show it again).
        "can_invite": can_invite,
        "link": glink_view(glink_active(g["id"])) if can_invite else None,
    }


# ---- 0.23.1 invite pack: rate limits, client address, group invite links ----
# Group links are Signal-style. The link is https://<site>/join#j1.<secret>; the part after "#" never reaches the
# server. From the secret the browser derives (HKDF) a proof and a card key. The server stores only sha256(proof),
# the group id, expiry, use counts and a revoked flag, plus two opaque ciphertexts it cannot open: the "card"
# (group name, sealed with the card key) and the "seal" (the secret itself, sealed with the group's epoch key so the
# admins who made it can show the link again on another device). No message key is ever in a link: joining moves
# the group to a new, empty epoch, which the joiner (or any member online) fills, so a link never unlocks history.
# See docs/INVITES.md. Older servers never read group-links.json, so a rollback to 0.23.0 still starts.
import ipaddress  # noqa: E402

GLINK_PATH = os.path.join(STATE_DIR, "group-links.json")
GLINK_EXPIRY = {"1d": 86400, "7d": 7 * 86400, "never": 0}
GLINK_MAX_USES = 100
GLINK_GONE = "This invite link has expired or was reset. Ask for a new one."
HEX64 = re.compile(r"^[0-9a-f]{64}$")
_glinks = []


# Read an hourly and a daily limit from the config (falling back to the defaults) as (count, seconds) pairs.
def _limit_pair(name_hour, hour_default, name_day, day_default):
    # A config number, at least 1; a bad value falls back to the default.
    def num(name, default):
        try:
            return max(1, int(bcconfig.get(name, str(default))))
        except (TypeError, ValueError):
            return default
    return ((num(name_hour, hour_default), 3600), (num(name_day, day_default), 86400))


INVITE_USER_LIMITS = _limit_pair("BC_INVITE_PER_USER_HOUR", 10, "BC_INVITE_PER_USER_DAY", 30)
INVITE_IP_LIMITS = _limit_pair("BC_INVITE_PER_IP_HOUR", 20, "BC_INVITE_PER_IP_DAY", 60)
REDEEM_USER_LIMITS = ((10, 600), (30, 86400))
REDEEM_IP_LIMITS = _limit_pair("BC_REDEEM_PER_IP_HOUR", 30, "BC_REDEEM_PER_IP_DAY", 100)


# Networks allowed to tell us the real client address (BC_TRUSTED_PROXIES), e.g. the Docker bridge.
def _proxy_nets():
    out = []
    for part in (bcconfig.get("BC_TRUSTED_PROXIES", "") or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                pass
    return out


TRUSTED_PROXY_NETS = _proxy_nets()


def client_ip_of(handler):
    """The visitor's address. CF-Connecting-IP is believed only from BC_TRUSTED_PROXIES (default: nobody).
    IPv6 is grouped by /64 because one person usually holds a whole /64."""
    raw = (getattr(handler, "client_address", None) or ("",))[0]
    try:
        peer = ipaddress.ip_address(raw)
        if peer.version == 6 and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
    except ValueError:
        return "unknown"
    ip = peer
    if any(peer in net for net in TRUSTED_PROXY_NETS):
        try:
            ip = ipaddress.ip_address((handler.headers.get("CF-Connecting-IP") or "").strip())
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
        except ValueError:
            ip = peer
    if ip.version == 6:
        return str(ipaddress.ip_network(str(ip) + "/64", strict=False))
    return str(ip)


_rate_lock = threading.Lock()
_rate_hits = {}


def rate_wait(bucket, who, limits):
    """Seconds until one more action is allowed (0 = allowed now). In memory: a restart forgets the counts."""
    now = time.time()
    longest = max(window for _, window in limits)
    with _rate_lock:
        hits = [t for t in _rate_hits.get((bucket, who), []) if now - t < longest]
        _rate_hits[(bucket, who)] = hits
        wait = 0
        for count, window in limits:
            recent = [t for t in hits if now - t < window]
            if len(recent) >= count:
                wait = max(wait, int(recent[-count] + window - now) + 1)
        return wait


# Remember one hit against a rate-limit bucket for this person or address.
def rate_note(bucket, who):
    with _rate_lock:
        _rate_hits.setdefault((bucket, who), []).append(time.time())


# Turn a wait in seconds into friendly words ("about 5 minutes") for error messages.
def wait_words(seconds):
    if seconds < 90:
        return "about a minute"
    if seconds < 3300:
        return "about %d minutes" % max(2, round(seconds / 60))
    hours = round(seconds / 3600)
    return "about %d hour%s" % (hours, "" if hours == 1 else "s")


# Load the group invite links from disk into memory at start-up.
def load_glinks():
    global _glinks
    data = read_json(GLINK_PATH, {})
    rows = data.get("links") if isinstance(data, dict) else None
    _glinks = [r for r in (rows or []) if isinstance(r, dict) and isinstance(r.get("lookup"), str)]


# Write the group invite links to disk, dropping ones that ended more than 30 days ago.
def save_glinks():
    now = time.time()
    # Forget links that ended more than 30 days ago so the file stays small.
    keep = []
    for r in _glinks:
        ended = r.get("revoked_ts") or (r.get("expires") or 0)
        if ended and now - ended > 30 * 86400 and (r.get("revoked") or (r.get("expires") and r["expires"] < now)):
            continue
        keep.append(r)
    _glinks[:] = keep
    atomic_write(GLINK_PATH, json.dumps({"version": 1, "links": _glinks}, separators=(",", ":")))


def glink_state(r, now=None):
    """'ok', or why the link no longer works."""
    now = now or time.time()
    if r.get("revoked"):
        return "reset"
    if r.get("expires") and r["expires"] < now:
        return "expired"
    if r.get("max_uses") and int(r.get("uses") or 0) >= int(r["max_uses"]):
        return "used"
    if not live_group(r.get("g")):
        return "gone"
    return "ok"


# The newest working invite link for a group, or None.
def glink_active(gid):
    for r in reversed(_glinks):
        if r.get("g") == gid and glink_state(r) == "ok":
            return r
    return None


# What a group admin may see about one invite link (no secrets: the lookup token stays on the server).
def glink_view(r):
    if not r:
        return None
    seal = r.get("seal") or {}
    return {
        "id": r.get("id") or "", "created": int(r.get("created") or 0), "expires": int(r.get("expires") or 0),
        "max_uses": int(r.get("max_uses") or 0), "uses": int(r.get("uses") or 0), "by": name_of(r.get("by") or ""),
        "seal": {"epoch": int(seal.get("epoch") or 0), "iv": seal.get("iv") or "", "ct": seal.get("ct") or ""},
    }


def glink_find(proof):
    """Look a link up by the proof the browser derived from the secret. Constant-time compare on the hash."""
    if not isinstance(proof, str) or not (32 <= len(proof) <= 64) or not re.match(r"^[A-Za-z0-9_-]+$", proof):
        return None
    digest = hashlib.sha256(proof.encode("ascii")).hexdigest()
    for r in _glinks:
        if hmac.compare_digest(r.get("lookup") or "", digest):
            return r
    return None


def name_of(key):
    """Display name for an account key. The key itself is used when the account is already gone."""
    user = accounts.find_user(key)
    if user:
        return user.get("username") or key
    return key


_reactions = None


def load_reactions():
    """Reaction emoji by message id. The revision counter tells clients to replace their copy."""
    global _reactions
    if isinstance(_reactions, dict):
        return _reactions
    data = read_json(REACT_PATH, {})
    if not isinstance(data, dict):
        data = {}
    rev = data.get("rev")
    if not isinstance(rev, int) or rev < 0:
        rev = 0
    items = data.get("items")
    if not isinstance(items, dict):
        items = {}
    clean = {}
    for mid, rows in items.items():
        if not isinstance(mid, str) or not HEX32.match(mid) or not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            emoji = row.get("emoji")
            user = row.get("user")
            if isinstance(emoji, str) and isinstance(user, str) and emoji and user:
                kept.append({"emoji": emoji, "user": user})
        if kept:
            clean[mid] = kept
    _reactions = {"rev": rev, "items": clean}
    return _reactions


def save_reactions(data):
    """Write reactions and keep the in-memory copy in step with the file."""
    global _reactions
    _reactions = data
    atomic_write(REACT_PATH, json.dumps(data, separators=(",", ":")))


_EMOJI_BASE = frozenset({
    0xa9, 0xae, 0x203c, 0x2049, 0x2122, 0x2139, 0x2194, 0x2195, 0x2196, 0x2197, 0x2198, 0x2199,
    0x21a9, 0x21aa, 0x231a, 0x231b, 0x2328, 0x23cf, 0x23e9, 0x23ea, 0x23eb, 0x23ec, 0x23ed, 0x23ee,
    0x23ef, 0x23f0, 0x23f1, 0x23f2, 0x23f3, 0x23f8, 0x23f9, 0x23fa, 0x24c2, 0x25aa, 0x25ab, 0x25b6,
    0x25c0, 0x25fb, 0x25fc, 0x25fd, 0x25fe, 0x2600, 0x2601, 0x2602, 0x2603, 0x2604, 0x260e, 0x2611,
    0x2614, 0x2615, 0x2618, 0x261d, 0x2620, 0x2622, 0x2623, 0x2626, 0x262a, 0x262e, 0x262f, 0x2638,
    0x2639, 0x2648, 0x2649, 0x264a, 0x264b, 0x264c, 0x264d, 0x264e, 0x264f, 0x2650, 0x2651, 0x2652,
    0x2653, 0x265f, 0x2668, 0x267b, 0x267e, 0x267f, 0x2692, 0x2693, 0x2694, 0x2696, 0x2697, 0x2699,
    0x269b, 0x269c, 0x26a0, 0x26a1, 0x26a7, 0x26aa, 0x26ab, 0x26b0, 0x26b1, 0x26bd, 0x26be, 0x26c4,
    0x26c5, 0x26c8, 0x26ce, 0x26cf, 0x26d3, 0x26d4, 0x26e9, 0x26ea, 0x26f0, 0x26f1, 0x26f2, 0x26f3,
    0x26f4, 0x26f5, 0x26f7, 0x26f8, 0x26f9, 0x26fa, 0x26fd, 0x2702, 0x2705, 0x2708, 0x2709, 0x270a,
    0x270b, 0x270c, 0x270d, 0x270f, 0x2712, 0x2714, 0x2716, 0x271d, 0x2721, 0x2728, 0x2733, 0x2734,
    0x2744, 0x2747, 0x274c, 0x274e, 0x2753, 0x2754, 0x2755, 0x2757, 0x2763, 0x2764, 0x2795, 0x2796,
    0x2797, 0x27a1, 0x27b0, 0x27bf, 0x2934, 0x2935, 0x2b05, 0x2b06, 0x2b07, 0x2b1b, 0x2b1c, 0x2b50,
    0x2b55, 0x3030, 0x303d, 0x3297, 0x3299, 0x1f170, 0x1f171, 0x1f17e, 0x1f17f, 0x1f18e, 0x1f191, 0x1f192,
    0x1f193, 0x1f194, 0x1f195, 0x1f196, 0x1f197, 0x1f198, 0x1f199, 0x1f19a, 0x1f201, 0x1f202, 0x1f21a, 0x1f22f,
    0x1f232, 0x1f233, 0x1f234, 0x1f235, 0x1f236, 0x1f237, 0x1f238, 0x1f239, 0x1f23a, 0x1f250, 0x1f251, 0x1f300,
    0x1f301, 0x1f303, 0x1f304, 0x1f305, 0x1f306, 0x1f307, 0x1f308, 0x1f309, 0x1f30a, 0x1f30b, 0x1f30c, 0x1f30d,
    0x1f30e, 0x1f30f, 0x1f310, 0x1f311, 0x1f312, 0x1f313, 0x1f314, 0x1f315, 0x1f316, 0x1f317, 0x1f318, 0x1f319,
    0x1f31a, 0x1f31b, 0x1f31c, 0x1f31d, 0x1f31e, 0x1f31f, 0x1f320, 0x1f321, 0x1f324, 0x1f325, 0x1f326, 0x1f327,
    0x1f328, 0x1f329, 0x1f32a, 0x1f32b, 0x1f32c, 0x1f32d, 0x1f32e, 0x1f32f, 0x1f330, 0x1f331, 0x1f332, 0x1f333,
    0x1f334, 0x1f335, 0x1f336, 0x1f337, 0x1f338, 0x1f339, 0x1f33a, 0x1f33b, 0x1f33c, 0x1f33d, 0x1f33e, 0x1f33f,
    0x1f340, 0x1f341, 0x1f342, 0x1f343, 0x1f344, 0x1f345, 0x1f346, 0x1f347, 0x1f348, 0x1f349, 0x1f34a, 0x1f34b,
    0x1f34c, 0x1f34d, 0x1f34e, 0x1f34f, 0x1f350, 0x1f351, 0x1f352, 0x1f353, 0x1f354, 0x1f355, 0x1f356, 0x1f357,
    0x1f358, 0x1f359, 0x1f35a, 0x1f35b, 0x1f35c, 0x1f35d, 0x1f35e, 0x1f35f, 0x1f360, 0x1f361, 0x1f362, 0x1f363,
    0x1f364, 0x1f365, 0x1f366, 0x1f367, 0x1f368, 0x1f369, 0x1f36a, 0x1f36b, 0x1f36c, 0x1f36d, 0x1f36e, 0x1f36f,
    0x1f370, 0x1f371, 0x1f372, 0x1f373, 0x1f375, 0x1f376, 0x1f377, 0x1f378, 0x1f379, 0x1f37a, 0x1f37b, 0x1f37c,
    0x1f37e, 0x1f37f, 0x1f380, 0x1f381, 0x1f382, 0x1f384, 0x1f385, 0x1f386, 0x1f387, 0x1f388, 0x1f389, 0x1f38a,
    0x1f38b, 0x1f38d, 0x1f38e, 0x1f38f, 0x1f390, 0x1f391, 0x1f396, 0x1f397, 0x1f399, 0x1f39a, 0x1f39b, 0x1f39e,
    0x1f39f, 0x1f3a0, 0x1f3a1, 0x1f3a2, 0x1f3a3, 0x1f3a4, 0x1f3a5, 0x1f3a6, 0x1f3a7, 0x1f3a8, 0x1f3aa, 0x1f3ab,
    0x1f3ac, 0x1f3ad, 0x1f3ae, 0x1f3af, 0x1f3b0, 0x1f3b1, 0x1f3b2, 0x1f3b3, 0x1f3b5, 0x1f3b6, 0x1f3b7, 0x1f3b8,
    0x1f3b9, 0x1f3ba, 0x1f3bb, 0x1f3bc, 0x1f3bd, 0x1f3be, 0x1f3bf, 0x1f3c0, 0x1f3c2, 0x1f3c3, 0x1f3c4, 0x1f3c5,
    0x1f3c6, 0x1f3c7, 0x1f3c8, 0x1f3c9, 0x1f3ca, 0x1f3cb, 0x1f3cc, 0x1f3cd, 0x1f3ce, 0x1f3cf, 0x1f3d0, 0x1f3d1,
    0x1f3d2, 0x1f3d3, 0x1f3d4, 0x1f3d5, 0x1f3d6, 0x1f3d7, 0x1f3d8, 0x1f3d9, 0x1f3da, 0x1f3db, 0x1f3dc, 0x1f3dd,
    0x1f3de, 0x1f3df, 0x1f3e0, 0x1f3e1, 0x1f3e2, 0x1f3e3, 0x1f3e4, 0x1f3e5, 0x1f3e6, 0x1f3e7, 0x1f3e8, 0x1f3e9,
    0x1f3ea, 0x1f3eb, 0x1f3ec, 0x1f3ed, 0x1f3ee, 0x1f3ef, 0x1f3f0, 0x1f3f5, 0x1f3f7, 0x1f3f8, 0x1f3f9, 0x1f3fa,
    0x1f400, 0x1f401, 0x1f402, 0x1f403, 0x1f404, 0x1f405, 0x1f406, 0x1f407, 0x1f408, 0x1f40a, 0x1f40b, 0x1f40c,
    0x1f40d, 0x1f40e, 0x1f40f, 0x1f410, 0x1f411, 0x1f412, 0x1f413, 0x1f414, 0x1f415, 0x1f416, 0x1f417, 0x1f418,
    0x1f419, 0x1f41a, 0x1f41b, 0x1f41c, 0x1f41d, 0x1f41e, 0x1f41f, 0x1f420, 0x1f421, 0x1f422, 0x1f424, 0x1f426,
    0x1f427, 0x1f428, 0x1f429, 0x1f42a, 0x1f42b, 0x1f42c, 0x1f42d, 0x1f42e, 0x1f42f, 0x1f430, 0x1f431, 0x1f433,
    0x1f434, 0x1f435, 0x1f436, 0x1f437, 0x1f438, 0x1f439, 0x1f43a, 0x1f43b, 0x1f43c, 0x1f43f, 0x1f440, 0x1f441,
    0x1f442, 0x1f443, 0x1f444, 0x1f445, 0x1f446, 0x1f447, 0x1f448, 0x1f449, 0x1f44a, 0x1f44b, 0x1f44c, 0x1f44d,
    0x1f44e, 0x1f44f, 0x1f450, 0x1f466, 0x1f467, 0x1f468, 0x1f469, 0x1f46e, 0x1f46f, 0x1f470, 0x1f471, 0x1f472,
    0x1f473, 0x1f474, 0x1f475, 0x1f476, 0x1f477, 0x1f478, 0x1f479, 0x1f47a, 0x1f47b, 0x1f47c, 0x1f47d, 0x1f47e,
    0x1f47f, 0x1f480, 0x1f481, 0x1f482, 0x1f483, 0x1f485, 0x1f486, 0x1f487, 0x1f488, 0x1f489, 0x1f48a, 0x1f48b,
    0x1f48c, 0x1f48e, 0x1f490, 0x1f492, 0x1f493, 0x1f494, 0x1f495, 0x1f496, 0x1f497, 0x1f498, 0x1f499, 0x1f49a,
    0x1f49b, 0x1f49c, 0x1f49d, 0x1f49e, 0x1f49f, 0x1f4a0, 0x1f4a1, 0x1f4a2, 0x1f4a3, 0x1f4a4, 0x1f4a5, 0x1f4a6,
    0x1f4a7, 0x1f4a8, 0x1f4a9, 0x1f4aa, 0x1f4ab, 0x1f4ac, 0x1f4ad, 0x1f4ae, 0x1f4af, 0x1f4b0, 0x1f4b1, 0x1f4b2,
    0x1f4b3, 0x1f4b4, 0x1f4b5, 0x1f4b6, 0x1f4b7, 0x1f4b8, 0x1f4b9, 0x1f4ba, 0x1f4bb, 0x1f4bc, 0x1f4be, 0x1f4bf,
    0x1f4c0, 0x1f4c1, 0x1f4c2, 0x1f4c5, 0x1f4c6, 0x1f4c7, 0x1f4c8, 0x1f4c9, 0x1f4ca, 0x1f4cb, 0x1f4cc, 0x1f4cd,
    0x1f4ce, 0x1f4cf, 0x1f4d0, 0x1f4db, 0x1f4dd, 0x1f4de, 0x1f4df, 0x1f4e0, 0x1f4e1, 0x1f4e2, 0x1f4e3, 0x1f4e4,
    0x1f4e5, 0x1f4e6, 0x1f4e7, 0x1f4e8, 0x1f4e9, 0x1f4ea, 0x1f4eb, 0x1f4ec, 0x1f4ed, 0x1f4ee, 0x1f4f1, 0x1f4f2,
    0x1f4f3, 0x1f4f4, 0x1f4f5, 0x1f4f6, 0x1f4f7, 0x1f4f8, 0x1f4f9, 0x1f4fa, 0x1f4fb, 0x1f4fc, 0x1f4fd, 0x1f4ff,
    0x1f500, 0x1f501, 0x1f502, 0x1f503, 0x1f504, 0x1f505, 0x1f506, 0x1f507, 0x1f508, 0x1f509, 0x1f50a, 0x1f50b,
    0x1f50c, 0x1f50f, 0x1f510, 0x1f511, 0x1f512, 0x1f513, 0x1f514, 0x1f515, 0x1f518, 0x1f519, 0x1f51a, 0x1f51b,
    0x1f51c, 0x1f51d, 0x1f51e, 0x1f51f, 0x1f520, 0x1f521, 0x1f522, 0x1f523, 0x1f524, 0x1f525, 0x1f526, 0x1f527,
    0x1f528, 0x1f529, 0x1f52a, 0x1f52b, 0x1f52c, 0x1f52d, 0x1f52e, 0x1f52f, 0x1f530, 0x1f531, 0x1f532, 0x1f533,
    0x1f534, 0x1f535, 0x1f536, 0x1f537, 0x1f538, 0x1f539, 0x1f53a, 0x1f53b, 0x1f53c, 0x1f53d, 0x1f549, 0x1f54a,
    0x1f54b, 0x1f54c, 0x1f54d, 0x1f54e, 0x1f56f, 0x1f570, 0x1f573, 0x1f575, 0x1f579, 0x1f57a, 0x1f587, 0x1f58a,
    0x1f58b, 0x1f58c, 0x1f58d, 0x1f590, 0x1f595, 0x1f596, 0x1f5a4, 0x1f5a5, 0x1f5a8, 0x1f5b1, 0x1f5b2, 0x1f5bc,
    0x1f5c2, 0x1f5c3, 0x1f5c4, 0x1f5d1, 0x1f5d2, 0x1f5d3, 0x1f5dc, 0x1f5dd, 0x1f5e1, 0x1f5e8, 0x1f5ef, 0x1f5f3,
    0x1f5fa, 0x1f5fb, 0x1f5fc, 0x1f5fd, 0x1f5fe, 0x1f5ff, 0x1f600, 0x1f601, 0x1f602, 0x1f603, 0x1f604, 0x1f605,
    0x1f606, 0x1f607, 0x1f608, 0x1f609, 0x1f60a, 0x1f60b, 0x1f60c, 0x1f60d, 0x1f60e, 0x1f60f, 0x1f610, 0x1f611,
    0x1f612, 0x1f613, 0x1f614, 0x1f615, 0x1f616, 0x1f617, 0x1f618, 0x1f619, 0x1f61a, 0x1f61b, 0x1f61c, 0x1f61d,
    0x1f61e, 0x1f61f, 0x1f620, 0x1f621, 0x1f622, 0x1f623, 0x1f624, 0x1f625, 0x1f626, 0x1f627, 0x1f628, 0x1f629,
    0x1f62a, 0x1f62b, 0x1f62c, 0x1f62d, 0x1f62e, 0x1f62f, 0x1f630, 0x1f631, 0x1f632, 0x1f633, 0x1f634, 0x1f635,
    0x1f636, 0x1f637, 0x1f641, 0x1f642, 0x1f644, 0x1f645, 0x1f646, 0x1f647, 0x1f648, 0x1f649, 0x1f64a, 0x1f64b,
    0x1f64c, 0x1f64d, 0x1f64e, 0x1f64f, 0x1f680, 0x1f681, 0x1f682, 0x1f683, 0x1f684, 0x1f685, 0x1f686, 0x1f687,
    0x1f688, 0x1f689, 0x1f68a, 0x1f68b, 0x1f68c, 0x1f68d, 0x1f68e, 0x1f68f, 0x1f690, 0x1f691, 0x1f692, 0x1f693,
    0x1f694, 0x1f695, 0x1f696, 0x1f697, 0x1f698, 0x1f699, 0x1f69a, 0x1f69b, 0x1f69c, 0x1f69d, 0x1f69e, 0x1f69f,
    0x1f6a0, 0x1f6a1, 0x1f6a2, 0x1f6a3, 0x1f6a4, 0x1f6a5, 0x1f6a6, 0x1f6a7, 0x1f6a8, 0x1f6aa, 0x1f6ab, 0x1f6ac,
    0x1f6ad, 0x1f6ae, 0x1f6af, 0x1f6b0, 0x1f6b1, 0x1f6b2, 0x1f6b3, 0x1f6b4, 0x1f6b5, 0x1f6b6, 0x1f6b7, 0x1f6b8,
    0x1f6b9, 0x1f6ba, 0x1f6bb, 0x1f6bc, 0x1f6bd, 0x1f6be, 0x1f6bf, 0x1f6c0, 0x1f6c1, 0x1f6c2, 0x1f6c3, 0x1f6c4,
    0x1f6c5, 0x1f6cb, 0x1f6cc, 0x1f6cd, 0x1f6ce, 0x1f6cf, 0x1f6d0, 0x1f6d1, 0x1f6d2, 0x1f6d5, 0x1f6d7, 0x1f6de,
    0x1f6e0, 0x1f6e1, 0x1f6e2, 0x1f6e3, 0x1f6e4, 0x1f6e5, 0x1f6e9, 0x1f6eb, 0x1f6ec, 0x1f6f0, 0x1f6f3, 0x1f6f4,
    0x1f6f5, 0x1f6f6, 0x1f6f7, 0x1f6f8, 0x1f6f9, 0x1f6fa, 0x1f6fb, 0x1f6fc, 0x1f7e0, 0x1f7e1, 0x1f7e2, 0x1f7e3,
    0x1f7e4, 0x1f7e5, 0x1f7e6, 0x1f7e7, 0x1f7e8, 0x1f7e9, 0x1f7ea, 0x1f7eb, 0x1f7f0, 0x1f90c, 0x1f90d, 0x1f90e,
    0x1f90f, 0x1f910, 0x1f911, 0x1f912, 0x1f913, 0x1f914, 0x1f915, 0x1f916, 0x1f917, 0x1f918, 0x1f919, 0x1f91a,
    0x1f91b, 0x1f91c, 0x1f91d, 0x1f91e, 0x1f91f, 0x1f920, 0x1f921, 0x1f922, 0x1f923, 0x1f924, 0x1f925, 0x1f926,
    0x1f928, 0x1f929, 0x1f92a, 0x1f92b, 0x1f92c, 0x1f92d, 0x1f92e, 0x1f92f, 0x1f930, 0x1f931, 0x1f932, 0x1f933,
    0x1f934, 0x1f935, 0x1f936, 0x1f937, 0x1f938, 0x1f939, 0x1f93a, 0x1f93c, 0x1f93d, 0x1f93e, 0x1f93f, 0x1f940,
    0x1f941, 0x1f942, 0x1f943, 0x1f945, 0x1f947, 0x1f948, 0x1f949, 0x1f94a, 0x1f94b, 0x1f94c, 0x1f94d, 0x1f94e,
    0x1f94f, 0x1f950, 0x1f951, 0x1f952, 0x1f953, 0x1f954, 0x1f955, 0x1f956, 0x1f957, 0x1f958, 0x1f959, 0x1f95a,
    0x1f95b, 0x1f95c, 0x1f95d, 0x1f95e, 0x1f95f, 0x1f960, 0x1f964, 0x1f965, 0x1f966, 0x1f967, 0x1f968, 0x1f969,
    0x1f96a, 0x1f96b, 0x1f96c, 0x1f96d, 0x1f96e, 0x1f96f, 0x1f970, 0x1f971, 0x1f973, 0x1f974, 0x1f975, 0x1f976,
    0x1f977, 0x1f978, 0x1f979, 0x1f97a, 0x1f980, 0x1f981, 0x1f983, 0x1f984, 0x1f985, 0x1f986, 0x1f987, 0x1f988,
    0x1f989, 0x1f98a, 0x1f98b, 0x1f98c, 0x1f98d, 0x1f98e, 0x1f98f, 0x1f990, 0x1f991, 0x1f992, 0x1f993, 0x1f994,
    0x1f998, 0x1f999, 0x1f99a, 0x1f99b, 0x1f99c, 0x1f99d, 0x1f99e, 0x1f9a0, 0x1f9a1, 0x1f9a2, 0x1f9a4, 0x1f9a5,
    0x1f9a6, 0x1f9a7, 0x1f9a8, 0x1f9aa, 0x1f9ab, 0x1f9ac, 0x1f9b4, 0x1f9b5, 0x1f9b6, 0x1f9b8, 0x1f9b9, 0x1f9be,
    0x1f9c0, 0x1f9c1, 0x1f9c3, 0x1f9c4, 0x1f9c5, 0x1f9c6, 0x1f9c7, 0x1f9c8, 0x1f9c9, 0x1f9ca, 0x1f9cb, 0x1f9cd,
    0x1f9ce, 0x1f9cf, 0x1f9d0, 0x1f9d1, 0x1f9d2, 0x1f9d3, 0x1f9d4, 0x1f9d5, 0x1f9d6, 0x1f9d7, 0x1f9d8, 0x1f9d9,
    0x1f9da, 0x1f9db, 0x1f9dc, 0x1f9dd, 0x1f9de, 0x1f9df, 0x1f9e0, 0x1f9e1, 0x1f9e7, 0x1f9e8, 0x1f9e9, 0x1f9ea,
    0x1f9eb, 0x1f9ec, 0x1f9ed, 0x1f9ef, 0x1f9f0, 0x1f9f1, 0x1f9f2, 0x1f9f4, 0x1f9f8, 0x1f9f9, 0x1f9fa, 0x1f9fb,
    0x1f9fc, 0x1f9fd, 0x1f9ff, 0x1fa70, 0x1fa78, 0x1fa79, 0x1fa7a, 0x1fa80, 0x1fa81, 0x1fa82, 0x1fa83, 0x1fa84,
    0x1fa86, 0x1fa90, 0x1fa91, 0x1fa92, 0x1fa93, 0x1fa94, 0x1fa95, 0x1fa97, 0x1fa98, 0x1fa99, 0x1fa9a, 0x1fa9b,
    0x1fa9c, 0x1fa9d, 0x1fa9e, 0x1fa9f, 0x1faa0, 0x1faa3, 0x1faa4, 0x1faa5, 0x1faa6, 0x1faa7, 0x1faa8, 0x1fab1,
    0x1fab2, 0x1fab4, 0x1fab5, 0x1fab6, 0x1fac5, 0x1fad0, 0x1fad1, 0x1fad2, 0x1fad3, 0x1fad4, 0x1fad5, 0x1fad6,
    0x1fae1, 0x1fae4, 0x1fae5, 0x1faf0, 0x1faf1, 0x1faf2, 0x1faf3, 0x1faf4, 0x1faf5, 0x1faf6,
})


def emoji_ok(value):
    """Accept one emoji, including a joined sequence, and reject other text."""
    if not isinstance(value, str) or not (1 <= len(value) <= 16):
        return False
    n = len(value)

    # Read one emoji (with its joiners and modifiers) starting at this position; None at the end.
    def one(start):
        if start >= n:
            return None
        ch = value[start]
        cp = ord(ch)
        if ch in "0123456789#*":
            nxt = value[start + 1] if start + 1 < n else ""
            nxt2 = value[start + 2] if start + 2 < n else ""
            if nxt == "\uFE0F" and nxt2 == "\u20E3":
                return start + 3
            if nxt == "\u20E3":
                return start + 2
            return None
        if cp < 128 or 0x7F <= cp <= 0x9F:
            return None
        if cp in (0x200D, 0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= cp <= 0x1F3FF:
            return None
        if cp not in _EMOJI_BASE:
            return None
        pos = start + 1
        if pos < n and 0x1F3FB <= ord(value[pos]) <= 0x1F3FF:
            pos += 1
        if pos < n and value[pos] in ("\uFE0E", "\uFE0F"):
            pos += 1
        return pos

    nxt = one(0)
    if nxt is None:
        return False
    i = nxt
    while i < n and value[i] == "\u200D":
        nxt = one(i + 1)
        if nxt is None:
            return False
        i = nxt
    return i == n


def public_reactions(rows, viewer):
    """Group reactions for the browser. Names are visible. The message text is not."""
    order = []
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        emoji = row.get("emoji")
        user = row.get("user")
        if not isinstance(emoji, str) or not isinstance(user, str):
            continue
        group = grouped.get(emoji)
        if group is None:
            group = {"emoji": emoji, "names": [], "mine": False, "seen": set()}
            grouped[emoji] = group
            order.append(group)
        if user in group["seen"]:
            continue
        group["seen"].add(user)
        group["names"].append(name_of(user))
        if user == viewer:
            group["mine"] = True
    return [{"emoji": group["emoji"], "names": group["names"], "mine": group["mine"]} for group in order]


def visible_reactions(viewer, lobby):
    """Reactions the viewer may see. Deleted messages and rooms they are not in are left out."""
    data = load_reactions()
    out = {}
    items = data["items"]
    if items:
        by_id = {msg.get("id"): msg for msg in _messages}
        for mid, rows in items.items():
            msg = by_id.get(mid)
            if not msg or msg.get("deleted") or not can_see(msg, viewer, lobby):
                continue
            public = public_reactions(rows, viewer)
            if public:
                out[mid] = public
    return int(data["rev"]), out


def friends_of(key, social):
    """Account keys linked to this person. Order is the stored pair order."""
    found = []
    for pair in social["pairs"]:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        if pair[0] == key:
            found.append(pair[1])
        elif pair[1] == key:
            found.append(pair[0])
    return found


def make_friends(key, other_key):
    """Add a friend pair (same store _friend_close uses) and drop any requests between the two."""
    with _store_lock:
        social = load_social()
        social["requests"] = [
            item for item in social["requests"]
            if not (isinstance(item, dict) and {item.get("from"), item.get("to")} == {key, other_key})
        ]
        if not is_friend(key, other_key, social):
            social["pairs"].append(sorted([key, other_key]))
        save_social(social)


def is_friend(key, other, social):
    """True when the two accounts have accepted each other."""
    return other in friends_of(key, social)


def disk_ok(extra):
    """True when the state disk can hold this many more bytes plus about 1 GB free."""
    try:
        free = shutil.disk_usage(STATE_DIR).free
    except OSError:
        return False
    return free >= extra + FREE_FLOOR


def bytes_used():
    """Bytes in stored chat files. The 10 GB cap includes these."""
    total = 0
    for root in (BLOB_DIR, CHUNK_DIR):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    return total


def budget_ok(extra):
    """True when adding this many bytes stays inside the 10 GB pool."""
    return bytes_used() + extra <= STORE_BUDGET


def chunk_count(size):
    """How many 8 MB pieces a plaintext of this size becomes."""
    return (size + CHUNK - 1) // CHUNK


def expect_part(plain_size, index, chunks):
    """True when this piece index and size match the upload the browser started."""
    if index < chunks - 1:
        plain = CHUNK
    else:
        plain = plain_size - CHUNK * (chunks - 1)
    return plain + 28


def session_path(upload_id):
    """Metadata file for one in-progress upload."""
    return os.path.join(CHUNK_DIR, upload_id + ".json")


def part_path(upload_id, index):
    """One uploaded piece. It is not a message until finish claims it."""
    return os.path.join(CHUNK_DIR, "%s-%d.part" % (upload_id, index))


def drop_session(upload_id):
    """Remove this browser's login row so the cookie cannot be used again."""
    try:
        os.remove(session_path(upload_id))
    except OSError:
        pass
    prefix = upload_id + "-"
    try:
        names = os.listdir(CHUNK_DIR)
    except OSError:
        return
    for name in names:
        if name.startswith(prefix) and name.endswith(".part"):
            try:
                os.remove(os.path.join(CHUNK_DIR, name))
            except OSError:
                pass



OWNER_USERNAME = bcconfig.OWNER_USERNAME
LAST_ACTIVE_PATH = os.path.join(STATE_DIR, "last_active.json")
LAST_ACTIVE_EVERY = 300
_active_seen = {}
_active_lock = threading.Lock()


PRESENCE_SECONDS = 90
_presence = {}


def mark_present(user_key):
    """In-memory heartbeat: any signed-in request (the page polls /api/sync every few seconds)."""
    if user_key:
        now = time.time()
        was = _presence.get(user_key, 0)
        _presence[user_key] = now
        if now - was >= PRESENCE_SECONDS:
            bump_change()   # 26.10.8: someone came online; waiting browsers update their green dots now


def _presence_ticker():
    """26.10.8: wake waiting browsers when someone drops offline (presence expires without any request)."""
    last = set()
    while True:
        time.sleep(5)
        try:
            cutoff = time.time() - PRESENCE_SECONDS
            now_set = {k for k, ts in list(_presence.items()) if ts >= cutoff}
            if now_set != last:
                last = now_set
                bump_change()
        except Exception:
            pass


# 26.10.7: typing indicators. Memory only (never written to disk, never logged); a ping says only "typing in this chat",
# never what. Keyed by chat then account; a ping lasts TYPING_TTL seconds and a sent message clears it at once.
TYPING_TTL = 6
TYPING_MIN_GAP = 1.0     # pings faster than this from one account are accepted but not re-stamped (app sends every ~3 s)
_typing = {}             # chat key -> {user_key: expires_at}
_typing_last = {}        # user_key -> last accepted ping time (rate limit)
_typing_lock = threading.Lock()


def typing_chat_key(user_key, data):
    """The chat a typing ping names, or None when this account is not in it. Same membership rules as sending."""
    box = data.get("box") if isinstance(data, dict) else None
    if box == "dm":
        other = accounts.find_user(data.get("peer") or "") if isinstance(data.get("peer"), str) else None
        okey = (other or {}).get("username_key") or ""
        if not okey or okey == user_key or not is_friend(user_key, okey, load_social()) or publicmode.blocked_either(user_key, okey):
            return None
        return "dm:" + ":".join(sorted([user_key, okey]))
    if box == "group":
        gid = data.get("g") if isinstance(data.get("g"), str) else ""
        g = live_group(gid) if gid else None
        if not g or not isinstance(g["members"].get(user_key), dict):
            return None
        return "group:" + gid
    if box == "lobby":
        return "lobby" if isinstance(load_lobby()["members"].get(user_key), dict) else None
    return None


def typing_set(chat, user_key, on):
    now = time.time()
    with _typing_lock:
        row = _typing.setdefault(chat, {})
        if on:
            row[user_key] = now + TYPING_TTL
        else:
            row.pop(user_key, None)
        if not row:
            _typing.pop(chat, None)
    bump_change()


def typing_clear_user(chat, user_key):
    with _typing_lock:
        row = _typing.get(chat)
        if row:
            row.pop(user_key, None)
            if not row:
                _typing.pop(chat, None)
            bump_change()


def typing_view(viewer, lobby, groups):
    """Who else is typing in the chats this account is in: [{box, peer|g, who: [usernames]}]."""
    now = time.time()
    out = []
    with _typing_lock:
        for chat in list(_typing):
            row = _typing[chat]
            for k in [k for k, exp in row.items() if exp < now]:
                row.pop(k, None)
            if not row:
                _typing.pop(chat, None)
                continue
            who = [k for k in row if k != viewer]
            if not who:
                continue
            if chat.startswith("dm:"):
                pair = chat[3:].split(":")
                if viewer not in pair:
                    continue
                out.append({"box": "dm", "peer": name_of(who[0]), "who": [name_of(who[0])]})
            elif chat.startswith("group:"):
                gid = chat[6:]
                g = groups.get(gid)
                if not g:
                    continue
                who = [k for k in who if k in g["members"]]
                if who:
                    out.append({"box": "group", "g": gid, "who": sorted(name_of(k) for k in who)})
            elif chat == "lobby" and isinstance(lobby["members"].get(viewer), dict):
                who = [k for k in who if k in lobby["members"]]
                if who:
                    out.append({"box": "lobby", "who": sorted(name_of(k) for k in who)})
    return out


# 26.10.12: read receipts. Per chat and account, two watermarks: the highest message seq this account's app has
# received ("d") and has shown on screen ("s"). Only seq numbers are stored, never message content (E2E). DMs and
# groups only (not the lobby). 26.10.13: always on, no setting.
RECEIPTS_PATH = os.path.join(STATE_DIR, "receipts.json")
_receipts = None
_receipts_lock = threading.Lock()




def _receipts_store():
    global _receipts
    if _receipts is None:
        got = read_json(RECEIPTS_PATH, {})
        _receipts = got if isinstance(got, dict) else {}
    return _receipts


# 26.10.13 (Luke): pinned messages. A server flag per chat: only message ids (no content) are stored, in pins.json,
# newest pin last, at most PIN_MAX per chat. Any member of the chat (DM, group, or your own My Stash) can pin/unpin.
PINS_PATH = os.path.join(STATE_DIR, "pins.json")
PIN_MAX = 50
_pins_lock = threading.Lock()


def pin_chat_key(user_key, data):
    """Chat key for a pin request, or None when this account is not in that chat. My Stash = "dm:<me>:<me>"."""
    if isinstance(data, dict) and data.get("box") == "dm" and isinstance(data.get("peer"), str):
        other = accounts.find_user(data["peer"])
        if other and other.get("username_key") == user_key:
            return "dm:" + user_key + ":" + user_key
    return typing_chat_key(user_key, data) if isinstance(data, dict) and data.get("box") in ("dm", "group") else None


def pins_set(chat, mid, on):
    with _pins_lock:
        store = read_json(PINS_PATH, {})
        ids = [x for x in (store.get(chat) or []) if x != mid]
        if on:
            ids = (ids + [mid])[-PIN_MAX:]
        if ids:
            store[chat] = ids
        else:
            store.pop(chat, None)
        atomic_write(PINS_PATH, json.dumps(store))
    bump_change()   # so other devices' /api/sync picks it up
    return ids


def pins_view(viewer, groups):
    """[{box, peer|g, ids}] for the DMs, groups and My Stash this account is in."""
    out = []
    for chat, ids in read_json(PINS_PATH, {}).items():
        if chat.startswith("dm:"):
            pair = chat[3:].split(":")
            if len(pair) == 2 and viewer in pair:
                out.append({"box": "dm", "peer": name_of(pair[1] if pair[0] == viewer else pair[0]), "ids": ids})
        elif chat.startswith("group:") and chat[6:] in groups:
            out.append({"box": "group", "g": chat[6:], "ids": ids})
    return out


def receipts_mark(chat, user_key, d, s):
    """Raise this account's watermarks in one chat. True when something changed."""
    top = _messages[-1]["seq"] if _messages else 0
    d = max(0, min(int(d or 0), top))
    s = max(0, min(int(s or 0), top))
    d = max(d, s)
    with _receipts_lock:
        store = _receipts_store()
        row = store.setdefault(chat, {})
        old = row.get(user_key) or [0, 0]
        new = [max(old[0], d), max(old[1], s)]
        if new == old:
            return False
        row[user_key] = new
        atomic_write(RECEIPTS_PATH, json.dumps(store))
    bump_change()
    return True


def receipts_view(viewer, groups):
    """[{box, peer|g, who: [{u, d, s}]}] for the DMs and groups this account is in, """
    out = []
    with _receipts_lock:
        store = dict(_receipts_store())
    for chat, row in store.items():
        if not isinstance(row, dict):
            continue
        if chat.startswith("dm:"):
            pair = chat[3:].split(":")
            if viewer not in pair or len(pair) != 2:
                continue
            other = pair[1] if pair[0] == viewer else pair[0]
            w = row.get(other)
            if w:
                out.append({"box": "dm", "peer": name_of(other), "who": [{"u": name_of(other), "d": w[0], "s": w[1]}]})
        elif chat.startswith("group:"):
            g = groups.get(chat[6:])
            if not g:
                continue
            who = [{"u": name_of(k), "d": w[0], "s": w[1]} for k, w in row.items()
                   if k != viewer and k in g["members"] and isinstance(w, list)]
            if who:
                out.append({"box": "group", "g": chat[6:], "who": who})
    return out


def online_names_now():
    """Online means a request within the last 90 seconds, not merely an unexpired login."""
    cutoff = time.time() - PRESENCE_SECONDS
    disabled = accounts.disabled_keys()
    return [name_of(k) for k, ts in list(_presence.items()) if ts >= cutoff and k not in disabled]


def is_owner(user):
    """Owner tools are for one account only, checked on the server."""
    return bool(user) and (user.get("username_key") or "") == OWNER_USERNAME


def touch_active(user_key):
    """Record the last authenticated request, at most once every five minutes per account."""
    if not user_key:
        return
    now = int(time.time())
    with _active_lock:
        if now - _active_seen.get(user_key, 0) < LAST_ACTIVE_EVERY:
            return
        _active_seen[user_key] = now
        data = read_json(LAST_ACTIVE_PATH, {})
        if not isinstance(data, dict):
            data = {}
        data[user_key] = now
        try:
            atomic_write(LAST_ACTIVE_PATH, json.dumps(data))
        except OSError:
            pass


def last_active_map():
    """Tracked last-active times, falling back to each account's newest message time."""
    data = read_json(LAST_ACTIVE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    out = {}
    for msg in list(_messages):
        key = msg.get("sender")
        ts = msg.get("ts")
        if key and isinstance(ts, int) and ts > out.get(key, (0, ""))[0]:
            out[key] = (ts, "message")
    for key, ts in data.items():
        if isinstance(ts, int) and ts >= out.get(key, (0, ""))[0]:
            out[key] = (ts, "seen")
    return out


def drop_account_data(user_key):
    """Everything delete_account removes besides the login. Messages stay."""
    drop_user_links(user_key)
    delete_key_backup(user_key)
    webpush.drop_user(user_key)
    drop_avatar(user_key)
    set_display_name(user_key, "")
    set_name_color(user_key, "")
    set_contact(user_key, None)
    drop_prefs(user_key)


def drop_user_links(user_key):
    """Remove this account from friends, requests, and the lobby. The lobby epoch changes if they were in it."""
    with _store_lock:
        social = load_social()
        pairs = [
            pair for pair in social["pairs"]
            if not (isinstance(pair, list) and user_key in pair)
        ]
        requests = [
            item for item in social["requests"]
            if not (isinstance(item, dict) and (item.get("from") == user_key or item.get("to") == user_key))
        ]
        if pairs != social["pairs"] or requests != social["requests"]:
            social["pairs"] = pairs
            social["requests"] = requests
            save_social(social)
        devices = load_devices()
        kept = [row for row in devices if not (isinstance(row, dict) and row.get("user") == user_key)]
        if kept != devices:
            save_devices(kept)
        lobby = load_lobby()
        if user_key in lobby["members"]:
            lobby["members"].pop(user_key, None)
            lobby["epoch"] = int(lobby.get("epoch") or 0) + 1
            lobby["epochs"][str(lobby["epoch"])] = []
            save_lobby(lobby)
        for g in groups_of(user_key):
            group_leave(g, user_key)
        drop_user_sessions(user_key)


def drop_user_sessions(user_key):
    """Drop unfinished uploads owned by this account."""
    try:
        names = os.listdir(CHUNK_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(CHUNK_DIR, name)
        meta = read_json(path, {})
        if isinstance(meta, dict) and meta.get("user") == user_key:
            drop_session(name[:-5])


def sweep_old():
    """Delete unfinished uploads and unclaimed files older than a day."""
    now = time.time()
    try:
        names = os.listdir(CHUNK_DIR)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(CHUNK_DIR, name)
        try:
            if now - os.path.getmtime(path) > 86400:
                if name.endswith(".json") and HEX32.match(name[:-5]):
                    drop_session(name[:-5])
                elif name.endswith(".part"):
                    os.remove(path)
        except OSError:
            pass
    try:
        blob_names = os.listdir(BLOB_DIR)
    except OSError:
        return
    for name in blob_names:
        meta_path = os.path.join(BLOB_DIR, name, "meta.json")
        meta = read_json(meta_path, {})
        if not isinstance(meta, dict) or meta.get("message"):
            continue
        try:
            old = now - os.path.getmtime(meta_path) > 86400
        except OSError:
            old = False
        if old:
            shutil.rmtree(os.path.join(BLOB_DIR, name), ignore_errors=True)


def blob_meta(blob_id):
    """Metadata for one stored file, or None when the id is not 32 hex characters."""
    if not HEX32.match(blob_id or ""):
        return None
    meta = read_json(os.path.join(BLOB_DIR, blob_id, "meta.json"), {})
    return meta if isinstance(meta, dict) and meta.get("id") == blob_id else None


def can_see(msg, viewer, lobby):
    """Who may read a row. Lobby history starts at the epoch when this person joined."""
    if msg.get("box") == "dm":
        return viewer in (msg.get("a"), msg.get("b"))
    if msg.get("box") == "group":
        # 0.23.0: current members only, from the epoch they joined at.
        g = live_group(msg.get("g"))
        member = g["members"].get(viewer) if g else None
        if not isinstance(member, dict):
            return False
        return int(msg.get("epoch") or 0) >= int(member.get("epoch_from") or 1)
    if msg.get("box") != "lobby":
        return False
    member = lobby["members"].get(viewer)
    if not isinstance(member, dict):
        return False
    return int(msg.get("epoch") or 0) >= int(member.get("epoch_from") or 0)


def message_for(msg, viewer):
    """Public view of one row. 0.23.0: group rows add "g"; group notices carry event/actor/target names, no ciphertext."""
    if msg.get("box") == "group" and msg.get("event"):
        return {
            "seq": msg["seq"], "id": msg["id"], "box": "group", "g": msg.get("g") or "",
            "sender": "", "peer": "", "ts": msg.get("ts") or 0, "epoch": msg.get("epoch") or 0,
            "event": msg.get("event") or "",
            "actor": name_of(msg["actor"]) if msg.get("actor") else "",
            "target": name_of(msg["target"]) if msg.get("target") else "",
            **({"name_iv": msg.get("name_iv") or "", "name_ct": msg.get("name_ct") or ""} if msg.get("name_ct") else {}),
        }
    public = _message_core(msg, viewer)
    if msg.get("box") == "group":
        public["g"] = msg.get("g") or ""
    return public


def _message_core(msg, viewer):
    """Public view of one stored row.

    A deleted row keeps its id, seq, sender, and time so every client can
    draw a removed marker. The ciphertext, wraps, and file are already gone.
    """
    peer = ""
    if msg.get("box") == "dm":
        other = msg["b"] if msg.get("a") == viewer else msg.get("a")
        peer = name_of(other)
    if msg.get("deleted"):
        return {
            "seq": msg["seq"],
            "id": msg["id"],
            "box": msg["box"],
            "sender": msg.get("sender_name") or "",
            "peer": peer,
            "ts": msg.get("ts") or 0,
            "epoch": msg.get("epoch") or 0,
            "deleted": True,
        }
    wraps = []
    for wrap in msg.get("wraps") or []:
        if isinstance(wrap, dict):
            wraps.append({
                "device_id": wrap.get("device_id") or "",
                "eph_pub": wrap.get("eph_pub") or "",
                "iv": wrap.get("iv") or "",
                "ct": wrap.get("ct") or "",
            })
    blob = msg.get("blob") if isinstance(msg.get("blob"), dict) else None
    public = {
        "seq": msg["seq"],
        "id": msg["id"],
        "box": msg["box"],
        "sender": msg.get("sender_name") or "",
        "peer": peer,
        "ts": msg.get("ts") or 0,
        "epoch": msg.get("epoch") or 0,
        "iv": msg.get("iv") or "",
        "ct": msg.get("ct") or "",
        "wraps": wraps,
        "epoch_iv": msg.get("epoch_iv") or "",
        "epoch_ct": msg.get("epoch_ct") or "",
        "blob": blob,
    }
    edited = 0
    try:
        edited = int(msg.get("edited_ts") or 0)
    except (TypeError, ValueError):
        edited = 0
    if edited:
        public["edited_ts"] = edited
        public["edited_seq"] = edited_seq_of(msg)
    return public


def take_wraps(items, limit):
    """Validate device wraps. Returns None when the list is empty, too long, or malformed."""
    if not isinstance(items, list) or not (1 <= len(items) <= limit):
        return None
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        device_id = (item.get("device_id") or "").lower()
        if not HEX32.match(device_id) or device_id in seen:
            return None
        seen.add(device_id)
        eph = item.get("eph_pub") or ""
        iv = item.get("iv") or ""
        ct = item.get("ct") or ""
        if not (b64_ok(eph, 80, 400) and b64_ok(iv, 8, 64) and b64_ok(ct, 16, 400)):
            return None
        out.append({"device_id": device_id, "eph_pub": eph, "iv": iv, "ct": ct})
    return out


def device_owner(rows, device_id):
    """Account key that registered this device id, or an empty string."""
    for row in rows:
        if row.get("device_id") == device_id:
            return row.get("user") or ""
    return ""


def too_fast(user_key):
    """True when this account posted again inside one second."""
    now = time.time()
    if now - _last_post.get(user_key, 0) < 1:
        return True
    _last_post[user_key] = now
    return False


def lobby_view(lobby, viewer, devices):
    """What this member may know. needs_epoch means someone online must create the empty epoch key. missing is devices that holder should wrap."""
    member = lobby["members"].get(viewer)
    joined = isinstance(member, dict)
    epoch = int(lobby.get("epoch") or 0)
    members = []
    online = set(online_names_now())
    disabled = accounts.disabled_keys()
    for key, row in lobby["members"].items():
        if isinstance(row, dict):
            item = {"username": row.get("name") or name_of(key), "online": (row.get("name") or name_of(key)) in online}
            if key in disabled:
                # Still a member for room-key handoff, so nothing is lost on re-enable. The page hides them.
                item["online"] = False
                item["hidden"] = True
            members.append(item)
    members.sort(key=lambda item: item["username"].lower())
    my_ids = {row.get("device_id") for row in devices if row.get("user") == viewer}
    wraps = []
    epoch_from = int(member.get("epoch_from") or 0) if joined else 10 ** 9
    for number, rows in lobby.get("epochs", {}).items():
        try:
            number_i = int(number)
        except ValueError:
            continue
        if number_i < epoch_from or not isinstance(rows, list):
            continue
        for wrap in rows:
            if isinstance(wrap, dict) and wrap.get("user") == viewer and wrap.get("device_id") in my_ids:
                wraps.append({
                    "epoch": number_i,
                    "device_id": wrap.get("device_id") or "",
                    "eph_pub": wrap.get("eph_pub") or "",
                    "iv": wrap.get("iv") or "",
                    "ct": wrap.get("ct") or "",
                })
    current = lobby.get("epochs", {}).get(str(epoch), [])
    if not isinstance(current, list):
        current = []
    needs_epoch = bool(joined and epoch and not current)
    missing = []
    covered = {(wrap.get("user"), wrap.get("device_id")) for wrap in current if isinstance(wrap, dict)}
    i_hold = any(wrap.get("user") == viewer and wrap.get("device_id") in my_ids for wrap in current if isinstance(wrap, dict))
    if joined and i_hold:
        member_keys = set(lobby["members"])
        for row in devices:
            if row.get("user") in member_keys and (row.get("user"), row.get("device_id")) not in covered:
                missing.append({
                    "username": row.get("name") or name_of(row.get("user")),
                    "device_id": row.get("device_id"),
                    "public_key": row.get("public_key"),
                })
    return {
        "joined": joined,
        "epoch": epoch if joined else 0,
        "members": members,
        "wraps": wraps,
        "needs_epoch": needs_epoch,
        "missing": missing,
    }


def klipy_key():
    """GIF search key from a root-only file. Search text is visible to this server and to KLIPY."""
    try:
        with open(KLIPY_KEY_PATH, encoding="utf-8") as fh:
            key = fh.readline().strip()
    except OSError:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._~-]{8,200}", key):
        return ""
    return key


def klipy_allowed(user_key, bucket, limit):
    """Simple per-account cap so GIF search cannot be hammered."""
    now = time.time()
    slot = bucket + ":" + user_key
    with _klipy_lock:
        hits = [stamp for stamp in _klipy_hits.get(slot, []) if now - stamp < 60]
        if len(hits) >= limit:
            _klipy_hits[slot] = hits
            return False
        hits.append(now)
        _klipy_hits[slot] = hits
        return True


def klipy_query(text):
    """Search text sent to KLIPY, trimmed and length-capped."""
    if not isinstance(text, str):
        return ""
    kept = []
    for ch in text:
        code = ord(ch)
        if 32 <= code != 127:
            kept.append(ch)
    return "".join(kept).strip()[:60]


def klipy_static(url):
    """True when a GIF URL is on static.klipy.com. Other hosts are refused."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    return parsed.scheme == "https" and parsed.hostname == "static.klipy.com" and parsed.path.lower().endswith(".gif")


def klipy_api(path, query):
    """GET a KLIPY JSON endpoint. The key stays in the path and is not returned to the browser."""
    key = klipy_key()
    if not key:
        return None, "GIF search is not set up yet."
    url = "https://api.klipy.com/api/v1/%s/%s?%s" % (quote(key, safe=""), path, urlencode(query))
    req = urllib.request.Request(url, headers={"User-Agent": "huzz-chat", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read(1024 * 1024)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "GIF search is not available right now."
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, "GIF search is not available right now."
    if not isinstance(data, dict) or data.get("result") is False:
        return None, "GIF search is not available right now."
    return data, ""


def klipy_items(data):
    """Pull the result list out of a KLIPY payload."""
    payload = data.get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        rows = payload.get("data")
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    return rows if isinstance(rows, list) else []


def klipy_urls(item):
    """Preview and file URLs for one GIF, preferring the smaller rendition."""
    files = item.get("file") if isinstance(item, dict) else None
    found = []
    if not isinstance(files, dict):
        return found
    for name in KLIPY_RENDITIONS:
        formats = files.get(name)
        if not isinstance(formats, dict):
            continue
        gif = formats.get("gif")
        if not isinstance(gif, dict):
            continue
        url = gif.get("url") or ""
        if klipy_static(url):
            found.append(url)
    return found


KLIPY_PER_PAGE = 24
KLIPY_MAX_PAGE = 50


def klipy_search(user_key, text, page=1):
    """Trending when the query is empty, otherwise a search. Errors are sentences for the page.
    0.21.08: GIF paging. Returns (gifs, next_page_or_None, error); KLIPY pages are 1-based."""
    if not isinstance(page, int) or page < 1 or page > KLIPY_MAX_PAGE:
        return None, None, "That is the end of the GIFs."
    if not klipy_allowed(user_key, "search", 45):
        return None, None, "Wait a moment and search again."
    query = klipy_query(text)
    params = {
        "page": str(page),
        "per_page": str(KLIPY_PER_PAGE),
        "locale": "us",
        "content_filter": "high",
        "format_filter": "gif",
    }
    if query:
        params["q"] = query
        params["customer_id"] = hashlib.sha256(user_key.encode("utf-8")).hexdigest()[:16]
        path = "gifs/search"
    else:
        path = "gifs/trending"
    data, error = klipy_api(path, params)
    if error:
        return None, None, error
    gifs = []
    rows = klipy_items(data)
    for item in rows:
        if not isinstance(item, dict) or item.get("type") == "ad":
            continue
        slug = item.get("slug") or ""
        if not isinstance(slug, str) or not KLIPY_SLUG_RE.match(slug) or not klipy_urls(item):
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        with _klipy_lock:
            _klipy_seen[slug] = (time.time(), klipy_urls(item))
            if len(_klipy_seen) > 2000:
                for old in sorted(_klipy_seen, key=lambda name: _klipy_seen[name][0])[:500]:
                    _klipy_seen.pop(old, None)
        gifs.append({"slug": slug, "title": klipy_query(title) or "GIF"})
        if len(gifs) >= KLIPY_PER_PAGE:
            break
    meta = data.get("data") if isinstance(data.get("data"), dict) else {}
    has_next = meta.get("has_next")
    if not isinstance(has_next, bool):
        has_next = len(rows) >= KLIPY_PER_PAGE   # no flag: a full page means there may be more
    more = page + 1 if (has_next and rows and page < KLIPY_MAX_PAGE) else None
    return gifs, more, ""


def klipy_bytes(user_key, slug):
    """Download one GIF after an exact slug match. The short slug before -- is the items id."""
    if not isinstance(slug, str) or not KLIPY_SLUG_RE.match(slug):
        return None, "That GIF could not be added."
    with _klipy_lock:
        cached = _klipy_blobs.get(slug)
        seen = _klipy_seen.get(slug)
    if cached:
        return cached[1], ""
    if not klipy_allowed(user_key, "file", 300):   # 0.21.08: scrolling loads more thumbnails
        return None, "Wait a moment and try that GIF again."
    urls = list(seen[1]) if seen and time.time() - seen[0] < 6 * 3600 else []
    if not urls:
        # Search slugs carry a "--suffix" that gifs/items drops, so match on the part before it too.
        short = slug.split("--", 1)[0]
        data, error = klipy_api("gifs/items", {"slugs": slug, "format_filter": "gif"})
        if error:
            return None, "That GIF could not be added."
        for item in klipy_items(data):
            if not isinstance(item, dict) or item.get("type") == "ad":
                continue
            if item.get("slug") in (slug, short):
                urls = klipy_urls(item)
                break
    for url in urls:
        blob = download_gif(url)
        if blob.startswith(b"GIF87a") or blob.startswith(b"GIF89a"):
            with _klipy_lock:
                _klipy_blobs[slug] = (time.time(), blob)
                if len(_klipy_blobs) > 80:
                    for old in sorted(_klipy_blobs, key=lambda name: _klipy_blobs[name][0])[:20]:
                        _klipy_blobs.pop(old, None)
            return blob, ""
    return None, "That GIF could not be added."


def download_gif(url):
    """Fetch a GIF from the allowed host and require a GIF header and the size cap."""
    if not klipy_static(url):
        return b""
    req = urllib.request.Request(url, headers={"User-Agent": "huzz-chat"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final = urlparse(resp.geturl())
            if final.scheme != "https" or final.hostname != "static.klipy.com":
                return b""
            chunks = []
            total = 0
            while True:
                part = resp.read(64 * 1024)
                if not part:
                    break
                total += len(part)
                if total > GIF_MAX:
                    return b""
                chunks.append(part)
    except (urllib.error.URLError, TimeoutError, OSError):
        return b""
    return b"".join(chunks)


def parse_multipart(content_type, body):
    """Split one browser upload. Used by older single-shot paths. Chat files now use pieces."""
    if not content_type or "multipart/form-data" not in content_type.lower():
        return None
    match = re.search(r"boundary=([^;]+)", content_type, re.I)
    if not match:
        return None
    boundary = match.group(1).strip().strip('"')
    if not boundary or len(boundary) > 200:
        return None
    try:
        marker = b"--" + boundary.encode("ascii")
    except UnicodeEncodeError:
        return None
    fields = {}
    file_part = None
    for part in body.split(marker):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        disposition = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition"):
                disposition = line
        name_match = re.search(r'name="([^"]*)"', disposition)
        file_match = re.search(r'filename="([^"]*)"', disposition)
        name = name_match.group(1) if name_match else ""
        if file_match or name == "file":
            file_part = data
        elif name:
            fields[name] = data.decode("utf-8", "replace")
    return fields, file_part


# 26.10.3: "Frequently used" emoji, kept per account in the same preferences file as the notification tone.
# Two lists: "pick" (emoji put into messages) and "react" (emoji used as reactions). Each is {emoji: [times used,
# last used (unix seconds)]}. The app does the ranking; the server only keeps the lists tidy and small.
EMOJI_STYLES = ("system", "twemoji", "noto", "fluent")   # 26.10.4: emoji styles (system = the device's own emoji)
EMOJI_USE_MAX = 60          # emoji remembered per list (the least used fall off the end)
EMOJI_KEY_BAD = re.compile(r"[\x00-\x22\x24-\x29\x2b-\x2f\x3a-\x7f\s]")
EMOJI_KEY_NEED = re.compile(r"[\u00a9-\U0010ffff]")


def emoji_use_clean(value):
    out = {}
    if not isinstance(value, dict):
        return out
    now = int(time.time()) + 86400
    for key, item in list(value.items())[:200]:
        # An emoji is a short run of characters with no letters, digits, spaces or markup in it.
        # (Only "#", "*" and digits are allowed from plain ASCII, for keycap emoji like 1\ufe0f\u20e3.)
        if not isinstance(key, str) or not (0 < len(key) <= 16) or EMOJI_KEY_BAD.search(key) or not EMOJI_KEY_NEED.search(key):
            continue
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(n, int) and not isinstance(n, bool) for n in item)):
            continue
        count, last = max(0, min(item[0], 100000)), max(0, min(item[1], now))
        if count:
            out[key] = [count, last]
    return out


def emoji_use_merge(old, new):
    both = dict(old)
    for key, (count, last) in new.items():
        was = both.get(key, [0, 0])
        both[key] = [max(was[0], count), max(was[1], last)]
    keep = sorted(both.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)[:EMOJI_USE_MAX]
    return dict(keep)


def emoji_prefs_out(row):
    saved = row.get("emoji") if isinstance(row.get("emoji"), dict) else {}
    out = {"pick": emoji_use_clean(saved.get("pick")), "react": emoji_use_clean(saved.get("react"))}
    out["tab"] = saved.get("tab") if saved.get("tab") in ("emoji", "gif", "stickers") else ""
    out["style"] = saved.get("style") if saved.get("style") in EMOJI_STYLES else ""
    return out


# 26.10.4: bundled emoji pictures, ready to send (see Handler.emoji_style_file): path -> (mtime, raw, gzipped).
_EMOJI_GZ = {}

# ---------------------------------------------------------------- 26.10.10 browser -> app sign-in hand-off
HANDOFF_SECONDS = 120          # token life
HANDOFF_ALLOW_SECONDS = 1800   # how long after creating the account the browser may ask for one
HANDOFF_MAX_ISSUES = 5         # tokens per new account (repeated taps)
_handoff_lock = threading.Lock()
_handoff_tokens = {}           # sha256(token) -> (username_key, expires)
_handoff_allowed = {}          # username_key -> [allowed_until, issued_count]


def _handoff_prune(now):
    for h in [h for h, (_, exp) in _handoff_tokens.items() if exp < now]:
        _handoff_tokens.pop(h, None)
    for k in [k for k, (until, _) in _handoff_allowed.items() if until < now]:
        _handoff_allowed.pop(k, None)


def _handoff_allow(key):
    now = time.time()
    with _handoff_lock:
        _handoff_prune(now)
        _handoff_allowed[key] = [now + HANDOFF_ALLOW_SECONDS, 0]


def _handoff_issue(key):
    now = time.time()
    with _handoff_lock:
        _handoff_prune(now)
        allow = _handoff_allowed.get(key)
        if not allow or allow[1] >= HANDOFF_MAX_ISSUES:
            return None
        allow[1] += 1
        for h in [h for h, (k, _) in _handoff_tokens.items() if k == key]:
            _handoff_tokens.pop(h, None)        # only the newest token works
        token = secrets.token_urlsafe(32)
        _handoff_tokens[hashlib.sha256(token.encode()).hexdigest()] = (key, now + HANDOFF_SECONDS)
        return token


def _handoff_take(token):
    if not token or len(token) > 100:
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _handoff_lock:
        _handoff_prune(now)
        hit = _handoff_tokens.pop(h, None)     # single use
        if not hit:
            return None
        _handoff_allowed.pop(hit[0], None)     # after a successful hand-off no more tokens for this account
        return hit[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "huzz-chat"

    def log_message(self, fmt, *args):
        """Do not write request lines. They can contain cookies."""
        return

    def host_ok(self):
        """Only the configured host names (BC_PUBLIC_URL host plus BC_HOSTS) are served. Other Host headers get a not-found page."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in HOSTS

    def send_json(self, code, obj, cookie=None, clear_cookie=False, verify=None):
        """JSON response. Optional session and verification cookies are set here."""
        flush_pending()   # 26.10.8: this request's log change is on disk before it answers
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        data, enc = self._maybe_compress(code, data, "application/json", None)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if enc:
            self.send_header("Content-Encoding", enc)
        if code == 200:
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie_header(cookie))
        if verify:
            self.send_header("Set-Cookie", verify_cookie_header(verify))
        if clear_cookie:
            self.send_header("Set-Cookie", clear_cookie_header())
        self.end_headers()
        self.wfile.write(data)

    def _maybe_compress(self, code, body, content_type, extra):
        """26.10.8 perf: gzip (or brotli when the optional module is installed) for text bodies over 1 KB when the
        browser accepts it. Static files are compressed once and kept in memory (keyed by path, size and checksum)."""
        if code != 200 or len(body) < COMPRESS_MIN or getattr(self, "command", "GET") == "HEAD":
            return body, None
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype not in COMPRESSIBLE:
            return body, None
        if extra and any(k.lower() in ("content-encoding", "content-range") for k, _ in extra):
            return body, None
        accept = (self.headers.get("Accept-Encoding") or "").lower()
        enc = "br" if (_brotli and "br" in accept) else ("gzip" if "gzip" in accept else None)
        if not enc:
            return body, None
        static = bool(extra) and any(k == "Cache-Control" and ("max-age" in v or v == "no-cache") for k, v in extra)
        key = (enc, len(body), zlib.crc32(body)) if static else None
        if key is not None:
            hit = _compress_cache.get(key)
            if hit is not None:
                return hit, enc
        if enc == "br":
            out = _brotli.compress(body, quality=9 if static else 4)
        else:
            out = gzip.compress(body, compresslevel=9 if static else 5, mtime=0)
        if len(out) >= len(body):
            return body, None
        if key is not None:
            with _compress_lock:
                if len(_compress_cache) > 256:
                    _compress_cache.clear()
                _compress_cache[key] = out
        return out, enc

    def send_bytes(self, code, body, content_type, extra=None):
        """A raw body with an explicit length. Used for files and pictures."""
        flush_pending()
        body, enc = self._maybe_compress(code, body, content_type, extra)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        if enc:
            self.send_header("Content-Encoding", enc)
        if code == 200 and (content_type or "").split(";")[0].strip().lower() in COMPRESSIBLE:
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra:
            for key, value in extra:
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 0.21.08.1: the browser stopped wanting this file (a GIF scrolled away, a new search). Not an error.
            self.close_connection = True

    def send_file(self, path, content_type, cache, headers=None, variant=None, me_name=None, me_user=None):
        """Read a file from disk and send it. HTML also gets the content security policy.
        variant: "fd" = the front door (login.html rebuilt by frontdoor.py), "get" = the old download page at /get.
        me_name: for the signed-in app page, the name written straight into the bottom-left account bar."""
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json(404, {"error": "That page was not found."})
            return
        if path.endswith((".html", ".webmanifest", "sw.js")):
            body = stamp_version(body)   # 26.10.8: version placeholders from the one VERSION line
        if _REWRITE_PAGES and (path.endswith(".html") or path.endswith(".webmanifest")):
            body = rewrite_page(path, body)
        body = apply_credits(path, body)   # optional per-server personal credits; no-op without BC_CREDITS_FILE
        if me_name:
            # 26.10.2: the account bar shows the real name from the very first paint, even if the page script
            # stops early (it used to keep the placeholder word "Account" until the first sync finished).
            body = body.replace(b'<span id="me-name">Account</span>',
                                ('<span id="me-name" data-user="%s">%s</span>' % (_html_escape(me_user or ""), _html_escape(me_name))).encode("utf-8"), 1)
        csp = CSP
        extra = [("Cache-Control", cache)]
        if variant == "fd":
            query = parse_qs(urlparse(self.path).query)
            body, csp, _app = frontdoor.page(body, csp, self.headers.get("User-Agent") or "", query)
            extra.append(("Vary", "User-Agent"))
        else:
            if publicmode.ANY and path.endswith(".html"):
                body, csp = publicmode.page(os.path.basename(path), body, csp, signup=False if variant == "get" else None)
            if downloads.ENABLED and path.endswith("login.html"):
                body = downloads.page(os.path.basename(path), body, get_page=(variant == "get"))
        if path.endswith(".html"):
            extra.append(("Content-Security-Policy", csp))
        if headers:
            extra.extend(headers)
        if "no-store" not in cache:
            # 26.10.8 perf: validator for everything cacheable, so a revalidation is a 304, not a full download.
            etag = '"%x-%x"' % (len(body), zlib.crc32(body))
            extra.append(("ETag", etag))
            inm = self.headers.get("If-None-Match") or ""
            if etag in [t.strip().removeprefix("W/") for t in inm.split(",")]:
                self.send_response(304)
                for key, value in extra:
                    if key in ("Cache-Control", "ETag", "Vary"):
                        self.send_header(key, value)
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_bytes(200, body, content_type, extra)

    def _prepare_body(self):
        """Record how many body bytes are still unread. A huge body is rejected before it is read."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > PLAIN_MAX + 1024 * 1024:
            self._unread = -1
            return False
        self._unread = length
        return True

    def _read_exact(self, length):
        """Read the next unread body bytes and shrink the leftover count."""
        if length < 0 or getattr(self, "_unread", -1) < 0:
            self.close_connection = True
            return None
        if length > self._unread:
            self.close_connection = True
            return None
        if length == 0:
            return b""
        try:
            raw = self.rfile.read(length)
        except OSError:
            self._unread = -1
            self.close_connection = True
            return None
        self._unread -= len(raw)
        if len(raw) != length:
            self._unread = -1
            self.close_connection = True
            return None
        return raw

    def _finish_body(self):
        """Drain a leftover POST body. Otherwise a kept connection treats it as the next request line."""
        # A leftover body is read as the next request line on a kept connection.
        left = getattr(self, "_unread", 0)
        if left <= 0:
            return
        if left > 65536:
            self.close_connection = True
            self._unread = -1
            return
        if self._read_exact(left) is None:
            self.close_connection = True

    def read_json_body(self):
        """Read one JSON object up to the small JSON cap. Picture uploads do not use this."""
        if not hasattr(self, "_unread") and not self._prepare_body():
            return None
        length = self._unread
        if length < 0 or length > JSON_MAX:
            return None
        raw = self._read_exact(length)
        if not raw:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def user(self):
        """The signed-in account from the session cookie, or None."""
        return accounts.user_from_cookie(self.headers.get("Cookie"))

    def require(self):
        """The signed-in account, or a 401. Callers must return when this is None."""
        user = self.user()
        if not user:
            self.send_json(401, {"error": "Sign in first."})
        return user

    def do_GET(self):
        """HTTP GET entry."""
        self.route("GET")

    def do_POST(self):
        """HTTP POST entry. The body is drained even when the handler returns early."""
        if not self._prepare_body():
            self.close_connection = True
            self.send_json(400, {"error": "That request was not accepted."})
            return
        try:
            self.route("POST")
        finally:
            self._finish_body()

    def do_OPTIONS(self):
        """0.23.3: CORS preflight, only for the invite check (home origin) and the public server-info."""
        self._unread = 0
        path = self.path.split("?", 1)[0]
        if not self.host_ok() or path not in ("/api/invite/check", "/.well-known/branchchat"):
            self.send_bytes(404, b"", "text/plain", [("Cache-Control", "no-store")])
            return
        self.send_bytes(204, b"", "text/plain", self._cors_headers(path) + [("Access-Control-Max-Age", "600")])

    # Cross-site headers: server info is readable by anyone; the directory home may POST; everything else stays
    # same-site.
    def _cors_headers(self, path):
        origin = (self.headers.get("Origin") or "").strip()
        if path == "/.well-known/branchchat":
            return [("Access-Control-Allow-Origin", "*")]
        if origin and origin == directory.HOME_ORIGIN:
            return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                    ("Access-Control-Allow-Methods", "POST"), ("Access-Control-Allow-Headers", "Content-Type")]
        return [("Vary", "Origin")]

    # Send a JSON reply with extra headers (for example the CORS headers above).
    def send_json_extra(self, code, obj, extra):
        data = json.dumps(obj).encode("utf-8")
        self.send_bytes(code, data, "application/json; charset=utf-8", [("Cache-Control", "no-store")] + list(extra))

    def _dir_ip(self):
        """Rate-limit key only (kept in memory, never stored). A home behind cloudflared sees the tunnel's
        private address, so CF-Connecting-IP is used when the peer is a private or loopback address."""
        raw = (getattr(self, "client_address", None) or ("",))[0]
        try:
            import ipaddress as _ip
            peer = _ip.ip_address(raw)
            if (peer.is_private or peer.is_loopback) and self.headers.get("CF-Connecting-IP"):
                return self.headers.get("CF-Connecting-IP").strip()[:64]
        except ValueError:
            pass
        return raw

    def directory_routes(self, method, path, query):
        """0.23.3 directory + invite landing. Returns True when the request was answered."""
        if path == "/admin/v1" or path.startswith("/admin/v1/"):
            self.send_json(404, {"error": "not found"})   # the admin API only lives on the LAN-only listener
            return True
        if method == "GET" and path == "/.well-known/branchchat":
            self.send_json_extra(200, directory.well_known(PUBLIC_URL, BC_VERSION_LABEL),
                                 [("Access-Control-Allow-Origin", "*"), ("X-Content-Type-Options", "nosniff")])
            return True
        if method == "GET" and path == "/directory-icon":
            raw, kind = directory.org_icon()
            if not raw:
                self.send_json(404, {"error": "No icon."})
            else:
                self.send_bytes(200, raw, directory.ICON_TYPES[kind], [("Cache-Control", "public, max-age=3600"),
                                ("Content-Security-Policy", "default-src 'none'; sandbox"), ("Cross-Origin-Resource-Policy", "cross-origin")])
            return True
        if method == "POST" and path == "/api/invite/check":
            self.invite_check()
            return True
        if not directory.HOME_MODE:
            return False
        if method == "GET" and ((path == "/join" and query.get("s")) or path in ("/directory", "/directory/")):
            name = "join-home.html" if path == "/join" else "directory.html"
            self.send_home_page(name)
            return True
        if method == "GET" and path == "/api/directory":
            self.send_json_extra(200, {"ok": True, "ttl": directory.TTL, "every": directory.PING_SECONDS,
                                       "servers": directory.directory_list()},
                                 [("Access-Control-Allow-Origin", "*")])
            return True
        if method == "GET" and path == "/api/directory/lookup":
            if directory.rate_ip(self._dir_ip(), "lookup", 120, 600):
                self.send_json(429, {"error": "Too many tries. Wait a few minutes."})
                return True
            self.send_json(200, directory.lookup((query.get("s") or [""])[0]))
            return True
        if method == "POST" and path == "/api/directory/lease":
            wait = directory.rate_ip(self._dir_ip(), "lease", 120, 3600)
            if wait:
                self.send_json(429, {"error": "Too many pings from this address.", "retry": wait})
                return True
            data = self.read_json_body()
            code, reply = directory.accept_lease(data)
            self.send_json(code, reply)
            return True
        if method == "GET" and path.startswith("/directory/icon/"):
            raw, ctype = directory.icon_file(path.rsplit("/", 1)[-1])
            if not raw:
                self.send_json(404, {"error": "No icon."})
            else:
                self.send_bytes(200, raw, ctype, [("Cache-Control", "public, max-age=86400"),
                                ("Content-Security-Policy", "default-src 'none'; sandbox")])
            return True
        return False

    def send_home_page(self, name):
        """Landing and directory pages on the home: may read server-info and check invites on other servers."""
        try:
            with open(os.path.join(HERE, name), "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json(404, {"error": "That page was not found."})
            return
        body = stamp_version(body)
        # 26.10.3: the link-preview image needs this server's full address (see rewrite_page).
        if not bcconfig.is_default_public_url():
            body = body.replace(bcconfig.DEFAULT_PUBLIC_URL.encode(), PUBLIC_URL.encode())
        extra_src = " https:" + (" http:" if directory.ALLOW_HTTP else "")
        csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:%s; connect-src 'self'%s; "
               "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" % (extra_src, extra_src))
        self.send_bytes(200, body, "text/html; charset=utf-8", [("Cache-Control", "no-cache"), ("Content-Security-Policy", csp)])

    def invite_check(self):
        """0.23.3: is this invite token still good? Read-only. Same origin, or cross-site from the home only."""
        cors = self._cors_headers("/api/invite/check")
        if directory.rate_ip(self._dir_ip(), "invite-check", 60, 600):
            self.send_json_extra(429, {"error": "Too many tries. Wait a few minutes."}, cors)
            return
        data = self.read_json_body() or {}
        state, inviter, expires = accounts.invite_state(data.get("token") or "")
        out = {"ok": state == "valid", "state": state, "name": directory.org_name()}
        if state == "valid":
            out["expires"] = expires
            other = accounts.find_user(inviter) if inviter else None
            if other and other.get("password"):
                out["invited_by"] = other.get("username") or ""
            # Invite without email: older invites sent by email let the page fill the email in. Only same-site pages get it.
            if not any(h[0] == "Access-Control-Allow-Origin" for h in cors):
                out["email"] = accounts.invite_email(data.get("token") or "")
        else:
            out["error"] = accounts.GONE_WORDS.get(state, "That link is not valid.")
        self.send_json_extra(200, out, cors)

    def route(self, method):
        """Hide internal errors behind one sentence. The host check runs first (after /healthz)."""
        if method == "GET" and self.path.split("?", 1)[0] == "/healthz":
            # Liveness probe for Docker, systemd and the installer. No auth, no host check, no data.
            self.send_bytes(200, b"ok", "text/plain; charset=utf-8", [("Cache-Control", "no-store")])
            return
        if not self.host_ok():
            self.send_json(404, {"error": "That page was not found."})
            return
        try:
            self.dispatch(method)
        except Exception:
            import traceback; traceback.print_exc()
            try:
                self.send_json(500, {"error": "The page hit a problem. Try again."})
            except Exception:
                pass

    def dispatch(self, method):
        """Route table. Public files and login are before the session wall. Everything else requires a cookie."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if method == "GET" and path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        if method == "GET" and path == "/api/version":
            # Public, no secrets: lets apps check YY.MM compatibility (see docs/VERSIONING.md).
            self.send_json(200, {"version": bcconfig.VERSION, "api": bcconfig.API_LEVEL, "min_app": bcconfig.MIN_APP})
            return
        if downloads.ENABLED and downloads.route(self, method, path):
            return
        if self.directory_routes(method, path, query):
            return
        if featuremail.handle(self, method, path, query):  # 26.10.4: "Turn off feature news" link
            return
        if legalpage.route(self, method, path):  # licensing: /license pages, public on every server
            return
        if method == "GET" and path == "/about":
            # BC_ABOUT_REQUIRES_LOGIN=1 (private family servers): signed-out visitors go to sign-in, like /admin, so the
            # server's personal About section and icon credit are only ever shown to signed-in members.
            if bcconfig.ABOUT_REQUIRES_LOGIN and not self.user():
                self.send_bytes(302, b"", "text/plain", [("Location", "/"), ("Cache-Control", "no-store")])
                return
            self.send_file(os.path.join(HERE, "about.html"), "text/html; charset=utf-8", "no-cache")
            return
        if method == "GET" and path == "/patch-notes":
            # 26.10.2: one codebase, two audiences. The public home (branchchat.app: front door, directory home, public
            # mode or download links) shows the public patch notes; every other server keeps its own history.
            public_notes = publicmode.PUBLIC_MODE or frontdoor.ENABLED or directory.HOME_MODE or downloads.ENABLED
            self.send_file(os.path.join(HERE, "patch-notes-public.html" if public_notes else "patch-notes.html"),
                           "text/html; charset=utf-8", "no-cache")
            return
        if method == "GET" and path in ("/admin", "/admin/"):
            # 0.23.3-beta.15: the Administrator page. Signed-out visitors go to sign-in; everyone except the owner
            # gets the same "not found" as any unknown page, so the page is not even known to exist.
            user = self.user()
            if not user:
                self.send_bytes(302, b"", "text/plain", [("Location", "/"), ("Cache-Control", "no-store")])
                return
            if not is_owner(user):
                self.send_json(404, {"error": "That page was not found."})
                return
            self.send_file(os.path.join(HERE, "admin.html"), "text/html; charset=utf-8", "no-store")
            return
        if method == "GET" and path == "/app-login":
            # 26.10.10: browser -> app sign-in hand-off landing page (token is in the #fragment, never sent here).
            self.send_file(os.path.join(HERE, "app-login.html"), "text/html; charset=utf-8", "no-store")
            return
        if method == "GET" and path in ("/", "/welcome", "/join"):
            user = self.user()
            if path == "/welcome":
                self.send_file(os.path.join(HERE, "welcome.html"), "text/html; charset=utf-8", "no-store")
                return
            name = "index.html" if user else "login.html"
            self.send_file(os.path.join(HERE, name), "text/html; charset=utf-8", "no-store",
                           variant="fd" if (frontdoor.ENABLED and name == "login.html") else None,
                           me_name=account_bar_name(user) if name == "index.html" else None,
                           me_user=user.get("username") if name == "index.html" else None)
            return
        if method == "GET" and path in ("/get", "/get/") and frontdoor.ENABLED and downloads.ENABLED:
            # The old home page (download tiles + plain sign-in card), now one small "Get the app" link away.
            self.send_file(os.path.join(HERE, "login.html"), "text/html; charset=utf-8", "no-store", variant="get")
            return
        if method == "GET" and path in ("/favicon.ico", "/favicon.png"):
            kind = "image/png" if path.endswith(".png") else "image/x-icon"
            self.send_file(os.path.join(HERE, path[1:]), kind, "public, max-age=86400")
            return
        if method == "GET" and path == "/sw.js":
            self.send_file(
                os.path.join(HERE, "sw.js"),
                "application/javascript; charset=utf-8",
                "no-store",
                [("Service-Worker-Allowed", "/")],
            )
            return
        if method == "GET" and path == "/offline.html":
            # 26.10.6: the page the service worker shows when the network is down (cached at install).
            self.send_file(os.path.join(HERE, "offline.html"), "text/html; charset=utf-8", "no-cache")
            return
        if method == "GET" and path == "/manifest.webmanifest":
            self.send_file(
                os.path.join(HERE, "manifest.webmanifest"),
                "application/manifest+json",
                "no-store",
            )
            return
        if _BRANDING and method == "GET" and path == "/instance.css":
            self.send_bytes(200, instance_css().encode("utf-8"), "text/css; charset=utf-8", [("Cache-Control", "no-cache")])
            return
        if _BRANDING and method == "GET" and path == "/instance/background":
            self.instance_background()
            return
        if method == "GET" and path == "/.well-known/assetlinks.json":
            self.send_file(
                os.path.join(HERE, "assetlinks.json"),
                "application/json",
                "public, max-age=3600",
            )
            return
        if method == "GET" and (path.startswith("/static/") or path.startswith("/vendor/")):
            if path == "/static/filter-data.json" and publicmode.ANY and publicmode.route_open(self, method, path):
                return
            self.static(path)
            return
        if method == "GET" and path == "/share":
            self.share_target(query)
            return
        if method == "POST" and path == "/api/login":
            self.login()
            return
        if method == "POST" and path == "/api/login/code":
            self.login_code()
            return
        if method == "POST" and path == "/api/login/resend":
            self.login_resend()
            return
        if method == "POST" and path == "/api/login/finish":
            self.login_finish()
            return
        if method == "POST" and path == "/api/login/totp":
            self.login_totp()
            return
        if method == "POST" and path == "/api/password/reset/start":
            self.reset_start()
            return
        if method == "POST" and path == "/api/password/reset/finish":
            self.reset_finish()
            return
        if method == "POST" and path == "/api/logout":
            self.logout()
            return
        if method == "POST" and path == "/api/welcome":
            self.welcome()
            return
        if method == "POST" and path == "/api/handoff/create":
            self.handoff_create()
            return
        if method == "POST" and path == "/api/handoff/redeem":
            self.handoff_redeem()
            return
        if publicmode.ANY and publicmode.route_open(self, method, path):
            return
        user = self.require()
        if not user:
            return
        touch_active(user["username_key"])
        mark_present(user["username_key"])
        if publicmode.ANY and publicmode.route_user(self, method, path, user):
            return
        if method == "GET" and path == "/api/users":
            self.user_directory(user)
            return
        if path.startswith("/api/owner/"):
            if not is_owner(user):
                self.send_json(403, {"error": "Only the owner can use this."})
                return
            if method == "GET" and path == "/api/owner/users":
                self.owner_users(user)
            elif method == "POST" and path == "/api/owner/remove":
                self.owner_remove(user)
            elif method == "POST" and path == "/api/owner/resend":
                self.owner_resend(user)
            elif method == "POST" and path == "/api/owner/invite":
                self.invite(user)
            elif method == "POST" and path == "/api/owner/role":
                self.owner_role(user)
            elif method == "POST" and path == "/api/owner/disable":
                self.owner_disable(user)
            elif method == "POST" and path == "/api/owner/reset-email":
                self.owner_reset_email(user)
            else:
                self.send_json(404, {"error": "That page was not found."})
            return
        if path.startswith("/api/server-admin/"):
            # 0.23.3-beta.15: Administrator page API. adminpage.route checks owner, re-verification and same-origin.
            adminpage.route(self, method, path, query, user)
            return
        if method == "POST" and path == "/api/account/password":
            self.new_password(user)
            return
        if method == "POST" and path == "/api/account/totp/start":
            self.totp_start(user)
            return
        if method == "POST" and path == "/api/account/totp/confirm":
            self.totp_confirm(user)
            return
        if method == "POST" and path == "/api/account/totp/cancel":
            self.totp_cancel(user)
            return
        if method == "POST" and path == "/api/account/totp/disable":
            self.totp_disable(user)
            return
        if method == "POST" and path == "/api/account/totp/backup":
            self.totp_backup(user)
            return
        if method == "POST" and path == "/api/account/delete":
            self.delete_account(user)
            return
        if method == "POST" and path == "/api/account/key-backup":
            self.save_key_backup(user)
            return
        if method == "POST" and path == "/api/account/key-code":
            self.send_key_code(user)
            return
        if method == "POST" and path == "/api/account/key-restore":
            self.restore_key_backup(user)
            return
        if method == "POST" and path == "/api/session/extend":
            self.extend(user)
        elif method == "GET" and path == "/api/sync":
            self.sync(user, query)
        elif method == "GET" and path == "/api/wait":
            self.wait_change(query)
        elif method == "POST" and path == "/api/devices":
            self.save_device(user)
        elif method == "GET" and path == "/api/push/key":
            self.push_key(user)
        elif method == "POST" and path == "/api/push/subscribe":
            self.push_subscribe(user)
        elif method == "POST" and path == "/api/push/unsubscribe":
            self.push_unsubscribe(user)
        elif method == "POST" and path == "/api/invite":
            self.invite(user)
        elif method == "GET" and path == "/api/invites":
            self.invites(user)
        elif method == "POST" and path == "/api/invites/create":
            self.invite_link_only(user)
        elif method == "POST" and path == "/api/invites/resend":
            self.invite_resend(user)
        elif method == "POST" and path == "/api/invites/cancel":
            self.cancel_invite(user)
        elif method == "POST" and path == "/api/friends/request":
            self.friend_request(user)
            welcomebot.step(user["username_key"], "friend")  # 26.10.3 Helper bot tour
        elif method == "POST" and path == "/api/friends/accept":
            self.friend_accept(user)
        elif method == "POST" and path == "/api/friends/decline":
            self.friend_decline(user)
        elif method == "POST" and path == "/api/friends/nickname":
            # 26.10.5: set or clear (empty) the private nickname you use for one friend. Friends only.
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            found = accounts.find_user(data.get("friend")) if isinstance(data.get("friend"), str) else None
            nick = clean_display(data.get("nickname", ""))
            fkey = (found or {}).get("username_key") or ""
            if not fkey or not is_friend(user["username_key"], fkey, load_social()):
                self.send_json(404, {"error": "That person is not your friend."})
            elif nick is None:
                self.send_json(400, {"error": "Nicknames can be up to 32 characters."})
            else:
                set_nickname(user["username_key"], fkey, nick)
                self.send_json(200, {"ok": True, "nickname": nick})
        elif method == "POST" and path == "/api/bug-report":
            # 26.10.5: the "Report a bug" form. Bigger than the JSON cap because screenshots ride along as base64.
            if not hasattr(self, "_unread") and not self._prepare_body():
                self.send_json(413, {"error": "That report is too big."})
                return
            if self._unread > bugreports.MAX_BODY:
                self.close_connection = True
                self.send_json(413, {"error": "Those screenshots are too big. Try fewer or smaller ones."})
                return
            raw = self._read_exact(self._unread)
            try:
                data = json.loads((raw or b"").decode("utf-8"))
            except (UnicodeError, ValueError):
                data = None
            if not isinstance(data, dict):
                self.send_json(400, {"error": "That report could not be read."})
            else:
                code, reply = bugreports.submit(user, data, self.headers)
                self.send_json(code, reply)
        elif method == "POST" and path == "/api/friends/remove":
            self.friend_remove(user)
        elif method == "POST" and path == "/api/lobby/join":
            self.lobby_join(user)
            welcomebot.step(user["username_key"], "lobby")  # 26.10.3 Helper bot tour
        elif method == "POST" and path == "/api/lobby/leave":
            self.lobby_leave(user)
        elif method == "POST" and path == "/api/lobby/epoch":
            self.lobby_epoch(user)
        elif method == "POST" and path == "/api/lobby/wraps":
            self.lobby_wraps(user)
        elif method == "POST" and not GROUPS_ENABLED and path in _GROUPS_OFF_PATHS:
            self.send_json(403, {"error": "Group chats are turned off on this server."})
        elif method == "POST" and path == "/api/groups/create":
            self.group_create(user)
            welcomebot.step(user["username_key"], "group")  # 26.10.3 Helper bot tour
        elif method == "POST" and path == "/api/groups/add":
            self.group_add(user)
        elif method == "POST" and path == "/api/groups/remove":
            self.group_remove(user)
        elif method == "POST" and path == "/api/groups/leave":
            self.group_leave_api(user)
        elif method == "POST" and path == "/api/groups/epoch":
            self.group_epoch(user)
        elif method == "POST" and path == "/api/groups/wraps":
            self.group_wraps(user)
        elif method == "POST" and path == "/api/groups/rename":
            self.group_rename(user)
        elif method == "POST" and path == "/api/groups/meta":
            self.group_meta_refresh(user)
        elif method == "POST" and path == "/api/glinks/create":
            self.glink_create(user)
        elif method == "POST" and path == "/api/glinks/revoke":
            self.glink_revoke(user)
        elif method == "POST" and path == "/api/glinks/peek":
            self.glink_peek(user)
        elif method == "POST" and path == "/api/glinks/redeem":
            self.glink_redeem(user)
        elif method == "POST" and path == "/api/messages/react":
            self.react_message(user)
            welcomebot.step(user["username_key"], "react")  # 26.10.3 Helper bot tour
        elif method == "POST" and path == "/api/messages/delete":
            self.delete_message(user)
            welcomebot.step(user["username_key"], "delete_anim")  # 26.10.3 Helper bot tour
        elif method == "POST" and path == "/api/messages/edit":
            self.edit_message(user)
        elif method == "GET" and path == "/api/account/avatar":
            self.avatar_get(user)
        elif method == "GET" and path == "/api/avatar":
            self.avatar_of(user)
        elif method == "POST" and path == "/api/account/avatar":
            self.avatar_put(user)
            welcomebot.step(user["username_key"], "avatar")  # 26.10.3 Helper bot tour
        elif method == "GET" and path == "/api/account/stickers":
            # 26.10.4: this account's recently used stickers, so the row at the top of the Stickers tab follows it.
            row = load_prefs().get(user["username_key"])
            row = row if isinstance(row, dict) else {}
            self.send_json(200, {"ok": True, "recent": sticker_recent_clean(row.get("stickers"))})
        elif method == "POST" and path == "/api/account/stickers":
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            with _store_lock:
                store = load_prefs()
                row = store.get(user["username_key"])
                row = row if isinstance(row, dict) else {}
                # The device sends its newest-first list; anything only the server knew is kept after it.
                row["stickers"] = sticker_recent_clean(sticker_recent_clean(data.get("recent")) + sticker_recent_clean(row.get("stickers")))
                store[user["username_key"]] = row
                atomic_write(PREFS_PATH, json.dumps(store))
            self.send_json(200, {"ok": True, "recent": row["stickers"]})
        elif method == "GET" and path == "/api/account/display_name":
            self.send_json(200, {"ok": True, "display_name": load_display_names().get(user["username_key"], "")})
        elif method == "GET" and path == "/api/account/prefs":
            row = load_prefs().get(user["username_key"])
            row = row if isinstance(row, dict) else {}
            out = {"ok": True, "tone": row.get("tone") if row.get("tone") in TONES else "chime"}
            # 26.10.2: the delete animation is saved per account too ("snap"; empty = never chosen, so the app uses Take
            # turns). "snap_legacy_ok" tells the app whether this account is old enough to take over the delete animation
            # this browser remembered from before the change (accounts made after the update never inherit it).
            out["snap"] = row.get("snap") if isinstance(row.get("snap"), str) and SNAP_PICK_RE.match(row.get("snap")) else ""
            out["snap_legacy_ok"] = account_predates_snap_prefs(user["username_key"])
            out.update(sound_prefs_of(row))   # 26.10.3: button sound set + the two sound volumes
            out["joker"] = row.get("joker") is not False     # 26.10.8: Joker bot (#joke), default on
            out["quietdel"] = row.get("quietdel") is True     # 26.10.11: no animation on other people's deletes, default off
            out["linkprev"] = row.get("linkprev") is not False   # 26.10.12: link previews, default on
            out["apk_prompt_done"] = row.get("apk_prompt_done") is True   # 26.10.12: Android app pop-up shown once
            out["feature_emails"] = featuremail.wants_news(accounts.find_user(user["username_key"]))   # 26.10.11
            sa = row.get("send_anim")                         # 26.10.11: Send animations pick (None = never chosen)
            out["send_anim_demo"] = row.get("send_anim_demo") is True   # 26.10.11: one-time first-send demo already used
            ss = row.get("send_speed")
            out["send_speed"] = ss if isinstance(ss, (int, float)) and 0.2 <= ss <= 2 else 0.75   # 26.10.13
            out["send_anim"] = sa if isinstance(sa, str) and SEND_ANIM_RE.match(sa) else None
            # 26.10.10: What's new pop-up. Last version this account saw ("" = none), whether it is brand new on this
            # version (never shown, marked seen), and the key whatsnew.js files the browser's copy under.
            seen = row.get("whatsnew_seen")
            out["whatsnew_seen"] = seen if isinstance(seen, str) and WHATSNEW_VER_RE.match(seen) else ""
            out["whatsnew_new"] = account_is_new_for_whatsnew(user["username_key"])
            out["whatsnew_user"] = user["username_key"]
            self.send_json(200, out)
        elif method == "POST" and path == "/api/account/prefs":
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            # 26.10.2: either field may be sent on its own: "tone" (notification sound) and/or "snap" (delete animation).
            tone, snap = data.get("tone"), data.get("snap")
            # 26.10.3: "click" (button sound set), "vol_ui" and "vol_char" (0-100) may come on their own too.
            click = data.get("click")
            joker_pref = data.get("joker")     # 26.10.8: Joker bot true/false
            quietdel_pref = data.get("quietdel")   # 26.10.11: true/false
            social_prefs = {k: data.get(k) for k in ("linkprev", "apk_prompt_done") if data.get(k) is not None}   # 26.10.12
            fe_pref = data.get("feature_emails")   # 26.10.11: feature update emails on/off (stored on the account)
            if isinstance(fe_pref, bool) and featuremail.set_news(user["username_key"], fe_pref) and len([k for k in data if data.get(k) is not None]) == 1:
                self.send_json(200, {"ok": True, "feature_emails": fe_pref})
                return
            send_speed = data.get("send_speed")   # 26.10.13: send animation duration multiplier 0.2..2
            send_anim = data.get("send_anim")  # 26.10.11: Send animations pick ("" = Off)
            sa_demo = data.get("send_anim_demo")   # 26.10.11: true once the first-send demo has played (never reset)
            wn_seen = data.get("whatsnew_seen")   # 26.10.10: What's new pop-up, last version seen
            vols = {k: data.get(k) for k in SOUND_VOL_KEYS if data.get(k) is not None}
            bad_vol = any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100 for v in vols.values())
            if tone is None and snap is None and click is None and not vols and joker_pref is None and wn_seen is None and send_anim is None and send_speed is None and sa_demo is None and quietdel_pref is None and not social_prefs:
                self.send_json(400, {"error": "Nothing to save."})
            elif any(not isinstance(v, bool) for v in social_prefs.values()):
                self.send_json(400, {"error": "That setting was not saved."})
            elif click is not None and click not in CLICKS:
                self.send_json(400, {"error": "That button sound is not available."})
            elif (joker_pref is not None and not isinstance(joker_pref, bool)) or (quietdel_pref is not None and not isinstance(quietdel_pref, bool)):
                self.send_json(400, {"error": "That setting was not saved."})
            elif wn_seen is not None and not (isinstance(wn_seen, str) and WHATSNEW_VER_RE.match(wn_seen)):
                self.send_json(400, {"error": "That setting was not saved."})
            elif bad_vol:
                self.send_json(400, {"error": "Volume must be 0 to 100."})
            elif tone is not None and tone not in TONES:
                self.send_json(400, {"error": "That tone is not available."})
            elif snap is not None and not (isinstance(snap, str) and SNAP_PICK_RE.match(snap)):
                self.send_json(400, {"error": "That delete animation is not available."})
            elif sa_demo is not None and sa_demo is not True:
                self.send_json(400, {"error": "That setting was not saved."})
            elif send_speed is not None and not (isinstance(send_speed, (int, float)) and not isinstance(send_speed, bool) and 0.2 <= send_speed <= 2):
                self.send_json(400, {"error": "That speed was not saved."})
            elif send_anim is not None and not (isinstance(send_anim, str) and SEND_ANIM_RE.match(send_anim)):
                self.send_json(400, {"error": "That send animation is not available."})
            else:
                with _store_lock:
                    store = load_prefs()
                    row = store.get(user["username_key"])
                    row = row if isinstance(row, dict) else {}
                    if tone is not None:
                        row["tone"] = tone
                    if snap is not None:
                        row["snap"] = snap
                    if click is not None:
                        row["click"] = click
                    row.pop("typing", None); row.pop("receipts", None)   # 26.10.13: typing + read receipts always on; drop old settings
                    if joker_pref is not None:
                        row["joker"] = joker_pref
                    if send_anim is not None:
                        row["send_anim"] = send_anim
                    if send_speed is not None:
                        row["send_speed"] = round(float(send_speed), 2)
                    if sa_demo is True:
                        row["send_anim_demo"] = True
                    if wn_seen is not None:
                        row["whatsnew_seen"] = wn_seen
                    if quietdel_pref is not None:
                        row["quietdel"] = quietdel_pref
                    row.update(social_prefs)
                    row.update(vols)
                    store[user["username_key"]] = row
                    atomic_write(PREFS_PATH, json.dumps(store))
                self.send_json(200, dict({"ok": True, "tone": row.get("tone") if row.get("tone") in TONES else "chime", "snap": row.get("snap") or "", "joker": row.get("joker") is not False, "quietdel": row.get("quietdel") is True}, **sound_prefs_of(row)))
        elif method == "GET" and path == "/api/chats/mutes":
            self.send_json(200, {"ok": True, "mutes": mutes_of(user["username_key"]), "now": int(time.time())})
        elif method == "POST" and path == "/api/chats/mute":
            self.chat_mute(user)
        elif method == "POST" and path == "/api/chats/list":
            self.chat_list(user)
        elif method == "GET" and path == "/api/account/contact":
            row = contact_of(user["username_key"])
            found = accounts.find_user(user["username_key"]) or {}
            row["email"] = found.get("email") or ""
            self.send_json(200, dict(row, ok=True))
        elif method == "POST" and path == "/api/account/contact":
            self.contact_put(user)
        elif method == "GET" and path == "/api/account/name_color":
            self.send_json(200, {"ok": True, "color": load_name_colors().get(user["username_key"], ""), "palette": NAME_PALETTE})
        elif method == "POST" and path == "/api/account/name_color":
            self.name_color_put(user)
        elif method == "POST" and path == "/api/account/display_name":
            self.display_name_put(user)
        elif method == "GET" and path == "/api/app/android-latest":
            # 26.10.12: the Android APK of the CURRENT GitHub Latest release (tag cached by downloads.latest_tag).
            base = downloads.APP_RELEASES or "https://github.com/NexusMcNexis/branch-chat-releases"
            url = downloads.release_file_url(".apk")
            self.send_json(200, {"ok": True, "url": url, "page": base + "/releases/latest", "version": downloads.release_version()})
        elif method == "POST" and path == "/api/pins":
            # 26.10.13: {box, peer|g, id, on}. Members only; the id must be a message in that chat.
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            key = user["username_key"]
            chat = pin_chat_key(key, data)
            mid = data.get("id") if isinstance(data.get("id"), str) and len(data.get("id")) <= 64 else ""
            msg = message_by_id(mid) if chat and mid else None
            in_chat = bool(msg) and not msg.get("deleted") and (
                (chat.startswith("group:") and msg.get("box") == "group" and msg.get("g") == chat[6:]) or
                (chat.startswith("dm:") and msg.get("box") == "dm" and "dm:" + ":".join(sorted([msg.get("a") or "", msg.get("b") or ""])) == "dm:" + ":".join(sorted(chat[3:].split(":")))))
            if not chat or not in_chat:
                self.send_json(403, {"error": "You can't pin that message."})
                return
            self.send_json(200, {"ok": True, "ids": pins_set(chat, mid, data.get("on") is not False)})
        elif method == "POST" and path == "/api/receipts":
            # 26.10.12: {box, peer|g, d, s} seq watermarks only. Members only; always on (26.10.13).
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            key = user["username_key"]
            chat = typing_chat_key(key, data) if data.get("box") in ("dm", "group") else None
            d, sn = data.get("d", 0), data.get("s", 0)
            if not chat or not all(isinstance(v, int) and not isinstance(v, bool) for v in (d, sn)):
                self.send_json(403 if not chat else 400, {"error": "Not saved."})
                return
            receipts_mark(chat, key, d, sn)
            self.send_json(200, {"ok": True})
        elif method == "POST" and path == "/api/typing":
            # 26.10.7: {box, peer|g, on}. Members only; memory only. 26.10.13: always on.
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            key = user["username_key"]
            chat = typing_chat_key(key, data)
            if not chat:
                self.send_json(403, {"error": "You are not in that chat."})
                return
            on = data.get("on") is not False
            now = time.time()
            if on and now - _typing_last.get(key, 0) < TYPING_MIN_GAP:
                self.send_json(200, {"ok": True})
                return
            if on:
                _typing_last[key] = now
            typing_set(chat, key, on)
            self.send_json(200, {"ok": True})
        elif method == "POST" and path == "/api/messages":
            self.post_message(user)
        elif method == "POST" and path == "/api/messages/wraps":
            self.add_wraps(user)
        elif method == "POST" and path == "/api/blobs/start":
            self.blob_start(user)
        elif method == "POST" and path == "/api/blobs/part":
            self.blob_part(user)
        elif method == "POST" and path == "/api/blobs/finish":
            self.blob_finish(user)
        elif method == "GET" and path.startswith("/api/blobs/"):
            self.blob_get(user, path)
        elif method == "GET" and path == "/api/account/emoji":
            # 26.10.3: this account's emoji habits (see emoji_use_clean), so "Frequently used" in the emoji picker and the
            # seven quick reactions follow the account to every device instead of staying with one browser.
            row = load_prefs().get(user["username_key"])
            row = row if isinstance(row, dict) else {}
            self.send_json(200, {"ok": True, **emoji_prefs_out(row)})
        elif method == "POST" and path == "/api/account/emoji":
            data = self.read_json_body()
            data = data if isinstance(data, dict) else {}
            with _store_lock:
                store = load_prefs()
                row = store.get(user["username_key"])
                row = row if isinstance(row, dict) else {}
                old = row.get("emoji") if isinstance(row.get("emoji"), dict) else {}
                merged = {}
                # Each list is merged with what the server already has (the bigger count and the newer time win), so
                # two devices saving at about the same time never wipe out each other's emoji.
                for kind in ("pick", "react"):
                    merged[kind] = emoji_use_merge(emoji_use_clean(old.get(kind)), emoji_use_clean(data.get(kind)))
                tab = data.get("tab") if data.get("tab") in ("emoji", "gif", "stickers") else old.get("tab")
                if tab in ("emoji", "gif", "stickers"):
                    merged["tab"] = tab
                # 26.10.4: the emoji style this account picked (how emoji look to them; see EMOJI_STYLES).
                style = data.get("style") if data.get("style") in EMOJI_STYLES else old.get("style")
                if style in EMOJI_STYLES:
                    merged["style"] = style
                row["emoji"] = merged
                store[user["username_key"]] = row
                atomic_write(PREFS_PATH, json.dumps(store))
            self.send_json(200, {"ok": True, **emoji_prefs_out(row)})
        elif method == "GET" and path == "/api/gifs":
            self.gifs(user, query)
        elif method == "GET" and path == "/api/gifs/file":
            self.gif_file(user, query)
        elif method == "GET" and path == "/api/unfurl":
            self.unfurl_get(user, query)
        elif method == "GET" and path == "/api/unfurl/image":
            self.unfurl_image(user, query)
        else:
            self.send_json(404, {"error": "That page was not found."})

    def share_target(self, query):
        """Android share sheet entry (manifest share_target). Public, before the session wall.
        Pulls the first http(s) link from url, then text, then title, and hands it to the page
        as ?share=. Signed-out users land on the login page, which carries ?share= through sign-in.
        Nothing is stored or logged, and nothing is ever sent automatically."""
        # First value of a query string parameter, or "".
        def first(name):
            vals = query.get(name) or [""]
            return vals[0] if isinstance(vals[0], str) else ""
        parts = [first("url"), first("text"), first("title")]
        message = ""
        for part in parts:
            found = SHARE_URL_RE.search(part or "")
            if found:
                message = found.group(0).rstrip(".,;:!?)]}'\"")
                break
        if not message:
            message = " ".join(p.strip() for p in (first("title"), first("text")) if p and p.strip())
        message = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", message).strip()[:SHARE_MAX_CHARS]
        target = "/?" + urlencode({"share": message}) if message else "/"
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def instance_background(self):
        """The admin's custom background picture (BC_BACKGROUND). Public, like the default forest picture."""
        found = _background_file()
        if not found:
            self.send_json(404, {"error": "That page was not found."})
            return
        full, kind = found
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json(404, {"error": "That page was not found."})
            return
        self.send_bytes(200, body, kind, [("Cache-Control", "public, max-age=3600")])

    def static(self, path):
        """Serve CSS, JS, and the default smiley. Vendor files may be cached. Our files say no-cache, and Cloudflare still caches them for hours, so the page bumps the query string."""
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            self.send_json(404, {"error": "That page was not found."})
            return
        full = os.path.realpath(os.path.join(HERE, rel))
        root = os.path.realpath(HERE)
        if not full.startswith(root + os.sep):
            self.send_json(404, {"error": "That page was not found."})
            return
        types = {
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".wasm": "application/wasm",
            ".png": "image/png",
            ".jpg": "image/jpeg",  # 26.10.2: the email banner (static/email/banner-light.jpg)
            ".ico": "image/x-icon",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
            ".json": "application/json; charset=utf-8",  # 26.10.4: static/stickers/manifest.json
            ".woff": "font/woff",
            ".webm": "audio/webm",
            ".opus": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
        }
        ext = os.path.splitext(full)[1].lower()
        # 26.10.4: bundled emoji styles (pictures and each style's index.json) are cached long and sent compressed.
        if rel.startswith("static/emoji/") and ext in (".svg", ".json") and os.path.isfile(full):
            self.emoji_style_file(full, "image/svg+xml" if ext == ".svg" else "application/json; charset=utf-8")
            return
        if ext not in types or not os.path.isfile(full):
            self.send_json(404, {"error": "That page was not found."})
            return
        cache = "public, max-age=86400" if rel.startswith("vendor/") else "no-cache"
        if rel.startswith("static/stickers/") and ext == ".webp":
            cache = "public, max-age=604800"  # 26.10.4: a sticker picture never changes under the same name (new art = new name)
        if (parse_qs(urlparse(self.path).query or "").get("v") or [""])[0] == BC_ASSET_STAMP:
            # 26.10.8 perf: only URLs stamped with this release's version (index.html: ?v=<VERSION>, bumped every
            # release) are immutable. Hand-numbered ones (?v=1, ?v=2) keep no-cache + ETag, so editing them without a
            # bump can never leave browsers on an old copy.
            cache = "public, max-age=31536000, immutable"
        self.send_file(full, types[ext], cache)

    def emoji_style_file(self, full, ctype):
        """26.10.4: the bundled emoji pictures (static/emoji/<style>/). The app asks for them with ?v=<build>, so the
        browser may keep them for 30 days without asking again. They are sent gzip-compressed when the browser accepts
        that (about a quarter of the size; the picture is exactly the same), kept ready in memory after the first time."""
        import gzip   # only needed here
        try:
            stamp = os.path.getmtime(full)
            hit = _EMOJI_GZ.get(full)
            if not hit or hit[0] != stamp:
                with open(full, "rb") as fh:
                    raw = fh.read()
                hit = (stamp, raw, gzip.compress(raw, 9, mtime=0))
                if len(_EMOJI_GZ) > 6000:
                    _EMOJI_GZ.clear()
                _EMOJI_GZ[full] = hit
        except OSError:
            self.send_json(404, {"error": "That page was not found."})
            return
        extra = [("Cache-Control", "public, max-age=2592000"), ("Vary", "Accept-Encoding")]
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            self.send_bytes(200, hit[2], ctype, extra + [("Content-Encoding", "gzip")])
        else:
            self.send_bytes(200, hit[1], ctype, extra)

    def _finish_login(self, user, set_verify):
        """Set the session. Browser logins also refresh the verification cookie. Bots pass set_verify false."""
        record = accounts.find_user(user.get("username") or user.get("username_key") or "")
        if not record or record.get("disabled"):
            self.send_json(401, {"error": "That login was not accepted."})
            return
        token = accounts.make_session(user)
        verify = None
        if set_verify:
            verify = accounts.make_marked_token(user, "verify", accounts.VERIFY_SECONDS)
        self.send_json(200, {
            "ok": True,
            "username": user["username"],
            "key_backup": key_backup_exists(user["username_key"]),
        }, cookie=token, verify=verify)

    def _after_password(self, user, has_keys):
        """Session, or the email key gate. The password and any authenticator code already passed."""
        browser = isinstance(has_keys, bool)
        # Clients that omit has_keys (the bot) get a session and are not emailed.
        # A browser that already holds device keys, or still has a verification
        # cookie, also skips the code. Otherwise the session waits for that code.
        if (not browser) or has_keys or accounts.verify_cookie_ok(self.headers.get("Cookie"), user):
            self._finish_login(user, set_verify=browser)
            return
        status = email_code_for(user)
        ticket = accounts.make_marked_token(user, "login-code", 600)
        if status == "wait":
            self.send_json(200, {
                "need_code": True,
                "emailed": True,
                "ticket": ticket,
                "note": "A code was already emailed. It expires in 10 minutes.",
            })
            return
        if status == "soon":
            self.send_json(200, {
                "need_code": True,
                "emailed": False,
                "ticket": ticket,
                "error": "Wait a minute before asking for another code.",
            })
            return
        if status != "ok":
            self.send_json(200, {
                "need_code": True,
                "emailed": False,
                "ticket": ticket,
                "error": "The code was not emailed.",
            })
            return
        self.send_json(200, {"need_code": True, "emailed": True, "ticket": ticket})

    def login(self):
        """Check the password. Authenticator codes come next when that account turned them on. Then the key gate."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That login was not accepted."})
            return
        if publicmode.login_gate(self, data.get("username")):
            return
        user = accounts.authenticate(data.get("username") or "", data.get("password") or "")
        if not user:
            publicmode.login_failed(self, data.get("username"))
            self.send_json(401, {"error": "That login was not accepted."})
            return
        if accounts.totp_enabled(user):
            _arm_factor(user["username_key"])
            ticket = accounts.make_marked_token(user, "login-totp", 300)
            self.send_json(200, {"need_totp": True, "ticket": ticket})
            return
        self._after_password(user, data.get("has_keys"))

    def login_totp(self):
        """Second step of sign-in. The session cookie is still unset."""
        data = self.read_json_body() or {}
        ticket = data.get("ticket") if isinstance(data.get("ticket"), str) else ""
        user = accounts.open_login_ticket(ticket, "login-totp")
        if not user:
            self.send_json(400, {"error": "Sign in again."})
            return
        full = accounts.find_user(user["username"])
        if not full or not accounts.totp_enabled(full):
            self.send_json(400, {"error": "Sign in again."})
            return
        typed = data.get("code") if isinstance(data.get("code"), str) else ""
        if not _take_factor(full, typed):
            self.send_json(400, {"error": "That code was not accepted."})
            return
        self._after_password(full, data.get("has_keys"))

    def reset_start(self):
        """Ask for a reset code. The answer is the same whether or not that username exists.

        Mail goes out after the response is queued so a slow send does not
        tell the caller that the username was real.
        """
        data = self.read_json_body() or {}
        username = data.get("username") if isinstance(data.get("username"), str) else ""
        self.send_json(200, {"ok": True, "note": RESET_NOTE})
        try:
            self.wfile.flush()
        except OSError:
            return
        threading.Thread(target=_issue_reset, args=(username,), daemon=True).start()

    def reset_finish(self):
        """Set a new password from the email code. Two-factor still applies. The sealed backup is removed."""
        data = self.read_json_body() or {}
        username = data.get("username") if isinstance(data.get("username"), str) else ""
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        code = data.get("code") if isinstance(data.get("code"), str) else ""
        factor = data.get("factor") if isinstance(data.get("factor"), str) else ""
        if len(password) < 8 or len(password) > 200:
            self.send_json(400, {"error": "Use at least 8 characters."})
            return
        user = accounts.find_user(username)
        if not user or not user.get("password"):
            self.send_json(400, {"error": "That code was not accepted."})
            return
        status = _finish_reset(user, code, factor)
        if status == "totp":
            self.send_json(200, {"need_totp": True})
            return
        if status != "ok":
            self.send_json(400, {"error": "That code was not accepted."})
            return
        error = accounts.set_password(user["username"], password)
        if error:
            self.send_json(400, {"error": error})
            return
        delete_key_backup(user["username_key"])
        self.send_json(200, {
            "ok": True,
            "note": (
                "Password updated. Sign in with the new password. "
                "Sign in on a device you already use first, so your message keys are backed up again. "
                "On a new device, older messages may not open."
            ),
        })

    def login_code(self):
        """Accept the email code. A backup is returned and the session waits for finish. No backup means the session starts now."""
        data = self.read_json_body() or {}
        ticket = data.get("ticket") if isinstance(data.get("ticket"), str) else ""
        user = accounts.open_login_ticket(ticket, "login-code")
        if not user:
            self.send_json(400, {"error": "Sign in again."})
            return
        typed = data.get("code") if isinstance(data.get("code"), str) else ""
        payload = take_email_code(user["username_key"], typed)
        if payload is None:
            self.send_json(400, {"error": "That code was not accepted."})
            return
        if payload.get("ct"):
            self.send_json(200, {
                "ok": True,
                "finish": self._store_finish(user),
                "salt": payload.get("salt"),
                "iv": payload.get("iv"),
                "ct": payload.get("ct"),
            })
            return
        self._finish_login(user, set_verify=True)

    def login_resend(self):
        """Email another code for the same sign-in ticket. A finish nonce is dropped so an old one cannot complete."""
        data = self.read_json_body() or {}
        ticket = data.get("ticket") if isinstance(data.get("ticket"), str) else ""
        user = accounts.open_login_ticket(ticket, "login-code")
        if not user:
            self.send_json(400, {"error": "Sign in again."})
            return
        with _store_lock:
            store = load_key_store()
            store["finish"].pop(user["username_key"], None)
            save_key_store(store)
        status = email_code_for(user)
        if status in ("wait", "soon"):
            self.send_json(429, {"error": "Wait a minute before asking for another code."})
            return
        if status != "ok":
            self.send_json(400, {"error": "The code was not emailed."})
            return
        self.send_json(200, {"ok": True})

    def login_finish(self):
        """Set the session after the browser has stored the unlocked backup."""
        data = self.read_json_body() or {}
        ticket = data.get("ticket") if isinstance(data.get("ticket"), str) else ""
        user = accounts.open_login_ticket(ticket, "login-finish")
        if not user:
            self.send_json(400, {"error": "Sign in again."})
            return
        nonce = (user.get("_ticket") or {}).get("nonce") or ""
        if not isinstance(nonce, str) or not nonce:
            self.send_json(400, {"error": "Sign in again."})
            return
        with _store_lock:
            store = load_key_store()
            current = store["finish"].get(user["username_key"]) or {}
            now = int(time.time())
            try:
                matched = hmac.compare_digest(
                    current.get("hash") or "",
                    accounts.code_hash(user["username_key"], nonce),
                )
            except ValueError:
                matched = False
            fresh = bool(current) and now <= int(current.get("exp") or 0) and matched
            store["finish"].pop(user["username_key"], None)
            save_key_store(store)
        if not fresh:
            self.send_json(400, {"error": "Sign in again."})
            return
        self._finish_login(user, set_verify=True)

    def _store_finish(self, user):
        """One-time nonce so finish cannot be replayed. The cookie ticket carries it. The file stores a hash."""
        nonce = secrets.token_hex(16)
        now = int(time.time())
        with _store_lock:
            store = load_key_store()
            store["finish"][user["username_key"]] = {
                "hash": accounts.code_hash(user["username_key"], nonce),
                "exp": now + 600,
            }
            save_key_store(store)
        return accounts.make_marked_token(user, "login-finish", 600, {"nonce": nonce})

    def logout(self):
        """End this browser session. The verification cookie stays so the next sign-in on this browser is not emailed again."""
        user = self.user()
        if user:
            accounts.drop_session(user)
        self.send_json(200, {"ok": True}, clear_cookie=True)

    def new_password(self, user):
        """Change the password and stay signed in on this browser. The caller reseals the key backup."""
        data = self.read_json_body() or {}
        current = data.get("current") if isinstance(data.get("current"), str) else ""
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        error = accounts.change_password(user["username"], current, password)
        if error:
            self.send_json(400, {"error": error})
            return
        fresh = accounts.find_user(user["username"])
        if not fresh:
            self.send_json(400, {"error": "That account was not found."})
            return
        token = accounts.make_session(fresh)
        self.send_json(200, {"ok": True}, cookie=token)

    def totp_start(self, user):
        """Begin authenticator setup. The secret is not active until a code confirms it."""
        self.read_json_body()
        secret, error = accounts.begin_totp(user["username"])
        if error:
            self.send_json(400, {"error": error})
            return
        label = quote("BranchChat:" + user["username"])
        uri = "otpauth://totp/%s?secret=%s&issuer=%s&algorithm=SHA1&digits=6&period=30" % (
            label, secret, quote("BranchChat"),
        )
        self.send_json(200, {"ok": True, "secret": secret, "uri": uri})

    def totp_confirm(self, user):
        """Turn two-factor on. Backup codes are returned once."""
        data = self.read_json_body() or {}
        code = data.get("code") if isinstance(data.get("code"), str) else ""
        codes, error = accounts.confirm_totp(user["username"], code)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True, "codes": codes})

    def totp_cancel(self, user):
        """Drop a setup secret that was never confirmed."""
        self.read_json_body()
        accounts.cancel_totp_pending(user["username"])
        self.send_json(200, {"ok": True})

    def totp_disable(self, user):
        """Turn two-factor off after a current authenticator code or a backup code."""
        data = self.read_json_body() or {}
        code = data.get("code") if isinstance(data.get("code"), str) else ""
        error = accounts.disable_totp(user["username"], code)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True})

    def totp_backup(self, user):
        """Replace backup codes. The plaintext codes are shown once."""
        data = self.read_json_body() or {}
        code = data.get("code") if isinstance(data.get("code"), str) else ""
        codes, error = accounts.replace_backup_codes(user["username"], code)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True, "codes": codes})

    def user_directory(self, user):
        """Every completed, active account except self, marked friend or pending. Usernames only, no emails."""
        key = user["username_key"]
        social = load_social()
        friends = set(friends_of(key, social))
        pending = set()
        for item in social["requests"]:
            if isinstance(item, dict) and key in (item.get("from"), item.get("to")):
                pending.add(item.get("from"))
                pending.add(item.get("to"))
        rows = []
        for row in accounts.owner_user_rows():
            if not row.get("ready") or row.get("disabled") or row.get("key") == key:
                continue
            k = row.get("key")
            if publicmode.blocked_either(key, k):
                continue
            status = "friend" if k in friends else ("pending" if k in pending else "")
            rows.append({"username": row["username"], "status": status})
        rows.sort(key=lambda r: r["username"].lower())
        self.send_json(200, {"ok": True, "users": rows})

    def owner_users(self, user):
        """Owner list with role, state, and last active."""
        seen = last_active_map()
        names = load_display_names()  # 26.10.13.8: display names in the owner list
        rows = []
        for row in accounts.owner_user_rows():
            ts, src = seen.get(row.get("key"), (0, ""))
            row["last_active"] = ts
            row["last_active_source"] = src
            row["owner"] = row.get("key") == OWNER_USERNAME
            dn = names.get(row.get("key") or "")
            row["display_name"] = dn.strip() if isinstance(dn, str) else ""
            row.pop("key", None)
            rows.append(row)
        self.send_json(200, {"ok": True, "users": rows})

    # Owner tools: read the request body and find the account it names, or reply 400 and return None.
    def _owner_target(self):
        data = self.read_json_body() or {}
        name = data.get("username") if isinstance(data.get("username"), str) else ""
        target = accounts.find_user(name) if name else None
        if not target:
            self.send_json(400, {"error": "That account was not found."})
            return None, data
        return target, data

    def owner_remove(self, user):
        """Permanent removal of another account. Old messages stay."""
        target, data = self._owner_target()
        if not target:
            return
        if target["username_key"] == OWNER_USERNAME:
            self.send_json(400, {"error": "The owner account cannot be removed here."})
            return
        error = accounts.remove_user(target["username"])
        if error:
            self.send_json(400, {"error": error})
            return
        drop_account_data(target["username_key"])
        self.send_json(200, {"ok": True})

    def owner_resend(self, user):
        """Fresh link for a pending invite (wraps accounts.resend_invite)."""
        self.invite_resend(user)

    # Owner tool: make someone an admin or a member. The owner can't demote themselves.
    def owner_role(self, user):
        target, data = self._owner_target()
        if not target:
            return
        role = data.get("role")
        if target["username_key"] == OWNER_USERNAME and role != "admin":
            self.send_json(400, {"error": "The owner cannot demote themselves."})
            return
        error = accounts.set_role(target["username"], role)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True})

    # Owner tool: disable or re-enable an account (hidden and signed out, data kept). Not the owner's own.
    def owner_disable(self, user):
        target, data = self._owner_target()
        if not target:
            return
        flag = bool(data.get("disabled"))
        if target["username_key"] == OWNER_USERNAME and flag:
            self.send_json(400, {"error": "The owner cannot disable themselves."})
            return
        error = accounts.set_disabled(target["username"], flag)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True, "disabled": flag})

    # 26.10.13.8: owner/admin tool. Email someone a one-time password reset link that also names their username.
    def owner_reset_email(self, user):
        target, data = self._owner_target()
        if not target:
            return
        status = _issue_admin_reset(target, user)
        if status == "ok":
            self.send_json(200, {"ok": True})
        elif status == "no_email":
            self.send_json(400, {"error": "That account has no email address on file."})
        elif status == "wait":
            self.send_json(429, {"error": "A reset email went to that account less than a minute ago. Wait a moment."})
        elif status == "busy":
            self.send_json(429, {"error": "Too many reset emails right now. Try again in a few minutes."})
        else:
            self.send_json(502, {"error": "The email could not be sent. Check the mail settings."})

    def delete_account(self, user):
        """Remove the login after the username and password match. The last admin cannot be deleted. Message history stays."""
        if publicmode.FULL_DELETE:
            publicmode.delete_account(self, user)  # public instance: 2FA code too, and messages/files are removed
            return
        data = self.read_json_body() or {}
        typed = data.get("username") if isinstance(data.get("username"), str) else ""
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        if typed != user["username"]:
            self.send_json(400, {"error": "Type your username to confirm."})
            return
        record = accounts.find_user(user["username"])
        if not record or not accounts.password_matches(password, record.get("password")):
            self.send_json(400, {"error": "That password was not accepted."})
            return
        if user.get("role") == "admin":
            admins = [
                row for row in accounts.list_users()
                if row.get("role") == "admin" and row.get("ready") and not row.get("invite_id")
            ]
            if len(admins) < 2:
                self.send_json(400, {"error": "The last owner account stays."})
                return
        error = accounts.remove_user(user["username"])
        if error:
            self.send_json(400, {"error": error})
            return
        drop_user_links(user["username_key"])
        delete_key_backup(user["username_key"])
        webpush.drop_user(user["username_key"])
        drop_avatar(user["username_key"])
        set_display_name(user["username_key"], "")
        set_name_color(user["username_key"], "")
        set_contact(user["username_key"], None)
        drop_prefs(user["username_key"])
        self.send_json(200, {"ok": True}, clear_cookie=True)

    def save_key_backup(self, user):
        """Store a password-sealed device key from a browser that already opens messages. Replaces the older backup."""
        data = self.read_json_body() or {}
        salt = data.get("salt") or ""
        iv = data.get("iv") or ""
        ct = data.get("ct") or ""
        if not b64_ok(salt, 20, 40) or not b64_ok(iv, 16, 32) or not b64_ok(ct, 40, 8000):
            self.send_json(400, {"error": "That key backup was not saved."})
            return
        with _store_lock:
            store = load_key_store()
            store["backups"][user["username_key"]] = {
                "salt": salt,
                "iv": iv,
                "ct": ct,
                "updated": int(time.time()),
            }
            save_key_store(store)
        self.send_json(200, {"ok": True})

    def send_key_code(self, user):
        """Email a code for the older in-chat restore path. The page no longer calls this. Login uses email_code_for."""
        if not key_backup_exists(user["username_key"]):
            self.send_json(400, {"error": "Save a key backup on a device that already opens your messages."})
            return
        status = email_code_for(user)
        if status in ("wait", "soon"):
            self.send_json(429, {"error": "Wait a minute before asking for another code."})
            return
        if status != "ok":
            self.send_json(400, {"error": "The code was not emailed."})
            return
        self.send_json(200, {"ok": True})

    def restore_key_backup(self, user):
        """Return the sealed backup after a code. The page no longer calls this. Login unlocks the backup before the session."""
        data = self.read_json_body() or {}
        typed = data.get("code") if isinstance(data.get("code"), str) else ""
        payload = take_email_code(user["username_key"], typed)
        if not payload or not payload.get("ct"):
            self.send_json(400, {"error": "That code was not accepted."})
            return
        self.send_json(200, {
            "ok": True,
            "salt": payload.get("salt"),
            "iv": payload.get("iv"),
            "ct": payload.get("ct"),
        })

    def welcome(self):
        """Create an account from an invite and sign that browser in. The email code applies on a later sign-in."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That link was not accepted."})
            return
        inviter = accounts.invite_inviter(data.get("token") or "")
        # Invite without email: the email typed on the new-account page is required and becomes the account's email.
        email = data.get("email") if isinstance(data.get("email"), str) else ""
        error = accounts.accept_invite(data.get("token") or "", data.get("username") or "", data.get("password") or "", email)
        if error:
            self.send_json(400, {"error": error})
            return
        user = accounts.find_user(data.get("username") or "")
        if not user or not user.get("password"):
            self.send_json(400, {"error": "That account was not created."})
            return
        if inviter:
            other = accounts.find_user(inviter)
            if other and other.get("password") and other.get("username_key") != user.get("username_key"):
                make_friends(user["username_key"], other["username_key"])
        featuremail.new_user(user)  # 26.10.4: queue the latest "What's new" email for this brand-new account
        token = accounts.make_session(user)
        _handoff_allow(user["username_key"])   # 26.10.10: this new account may hand its sign-in to the Android app once
        self.send_json(200, {"ok": True, "username": user["username"]}, cookie=token)

    def handoff_create(self):
        """26.10.10: "Open in BranchChat app" after sign-up in a phone browser. Signed-in session required, and only
        within HANDOFF_ALLOW_SECONDS of creating the account on this server. Returns a random single-use token that
        is good for HANDOFF_SECONDS; only its hash is kept (memory only). The browser puts it in an app link #fragment."""
        user = self.require()
        if not user:
            return
        token = _handoff_issue(user["username_key"])
        if not token:
            self.send_json(403, {"error": "Open the app and sign in there."})
            return
        server = directory.host_key(directory.norm_origin(PUBLIC_URL) or PUBLIC_URL) if PUBLIC_URL.startswith("https://") else ""
        self.send_json(200, {"ok": True, "token": token, "expires_in": HANDOFF_SECONDS,
                             "home": directory.HOME_URL, "server": server})

    def handoff_redeem(self):
        """26.10.10: the app's WebView trades the hand-off token for its own session (single use, 2 minutes)."""
        data = self.read_json_body() or {}
        token = data.get("token") if isinstance(data.get("token"), str) else ""
        key = _handoff_take(token)
        record = accounts.find_user(key) if key else None
        if not record or record.get("disabled") or not record.get("password"):
            self.send_json(400, {"error": "That sign-in link expired. Sign in with your username and password."})
            return
        self._finish_login(record, set_verify=True)

    def extend(self, user):
        """Slide this browser's 400-day login forward. There is no idle logout on this site."""
        data = self.read_json_body() or {}
        try:
            seconds = int(data.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        token, exp = accounts.extend_session(user, seconds)
        if not token:
            self.send_json(400, {"error": "That login could not be extended."})
            return
        self.send_json(200, {"ok": True, "expires_in": max(0, int(exp - time.time()))}, cookie=token)

    def wait_change(self, query):
        """26.10.8 long-poll: hold until anything changes after change version `cv`, or up to `t` seconds (max 25).
        Answers {"cv": n, "changed": bool}; the browser then runs a normal /api/sync. Holds no store lock while waiting.
        While anyone is typing the wait is capped at 7 s so an expired typing line clears on time."""
        try:
            cv = int((query.get("cv") or ["-1"])[0])
        except ValueError:
            cv = -1
        try:
            t = float((query.get("t") or [str(WAIT_MAX)])[0])
        except ValueError:
            t = WAIT_MAX
        t = max(0.0, min(float(WAIT_MAX), t))
        if _typing:
            t = min(t, 7.0)
        end = time.time() + t
        with _change_cond:
            while _change_ver == cv:
                left = end - time.time()
                if left <= 0:
                    break
                _change_cond.wait(left)
            now = _change_ver
        self.send_json(200, {"ok": True, "cv": now, "changed": now != cv})

    def sync(self, user, query):
        """One poll: people, lobby, new messages, edits, and ids removed since the gone cursor. Bodies stay ciphertext."""
        try:
            welcomebot.ensure(user)  # 26.10.3: Helper bot friend + one welcome (only when switched on)
        except Exception:
            pass
        try:
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        if since < 0:
            since = 0
        try:
            gone = int((query.get("gone") or ["0"])[0])
        except ValueError:
            gone = 0
        if gone < 0:
            gone = 0
        try:
            edit_from = int((query.get("edit") or ["0"])[0])
        except ValueError:
            edit_from = 0
        if edit_from < 0:
            edit_from = 0
        try:
            react_since = int((query.get("react") or ["0"])[0])
        except ValueError:
            react_since = 0
        if react_since < 0:
            react_since = 0
        key = user["username_key"]
        cv_now = _change_ver   # 26.10.8: read before building the answer, so a change made meanwhile wakes the next wait
        # Disabled accounts are hidden from friends, requests, presence, and the
        # lobby member list. Their data and messages stay stored.
        disabled = accounts.disabled_keys()
        hidden = publicmode.hidden_senders(key)  # people this account blocked (public instance only)
        with _store_lock:
            # 26.10.8 perf: parsed state reused from memory; new rows found by binary search on seq; deleted and
            # edited rows read from their own sorted lists (no full-history scans on an idle poll).
            social = load_social_ro()
            devices = load_devices_ro()
            lobby = load_lobby_ro()
            friend_keys = friends_of(key, social)
            visible = []
            more = False
            for msg in rows_after_for(key, lobby, since):
                if not can_see(msg, key, lobby):
                    continue
                if hidden and msg.get("sender") in hidden:
                    continue
                if len(visible) >= SYNC_LIMIT:
                    more = True
                    break
                visible.append(message_for(msg, key))
            # seq does not move when a message is tombstoned, so clients that
            # already passed that seq learn about it from removed/gone.
            # deleted_seq is not message order. A partial page must be the
            # lowest cursors, or advancing gone would hide a later row.
            _ensure_change_idx()
            removed = []
            top_gone = gone
            for dseq, mid, gone_msg in _del_idx[_first_after(_del_idx, gone, lambda t: t[0]):]:
                if not gone_msg.get("deleted") or not can_see(gone_msg, key, lobby):
                    continue
                if len(removed) >= SYNC_LIMIT:
                    more = True
                    break
                removed.append(removed_item(gone_msg, dseq))
                top_gone = dseq
            # An edit keeps its seq, so clients that already stored the row
            # learn the new ciphertext from edited/edit. Deleted rows stay gone.
            edited = []
            top_edit = edit_from
            for eseq, _seq, msg in _edit_idx[_first_after(_edit_idx, edit_from, lambda t: t[0]):]:
                if msg.get("deleted") or edited_seq_of(msg) != eseq or not can_see(msg, key, lobby):
                    continue
                if hidden and msg.get("sender") in hidden:
                    continue
                if len(edited) >= SYNC_LIMIT:
                    more = True
                    break
                edited.append(message_for(msg, key))
                top_edit = eseq
            lobby_out = lobby_view(lobby, key, devices)
            allowed = set(friend_keys)
            allowed.add(key)
            if key in lobby["members"]:
                allowed.update(lobby["members"])
            online_set = set(online_names_now())
            groups_out = []
            for g in groups_of(key):
                groups_out.append(group_view(g, key, devices, online_set, disabled))
                allowed.update(g["members"])
            device_out = []
            for row in devices:
                if row.get("user") in allowed:
                    device_out.append({
                        "username": row.get("name") or name_of(row.get("user")),
                        "device_id": row.get("device_id"),
                        "public_key": row.get("public_key"),
                    })
            incoming = []
            outgoing = []
            for item in social["requests"]:
                if not isinstance(item, dict):
                    continue
                if item.get("from") in disabled or item.get("to") in disabled:
                    continue
                if item.get("to") == key:
                    incoming.append({"username": name_of(item.get("from"))})
                elif item.get("from") == key:
                    outgoing.append({"username": name_of(item.get("to"))})
            online_names = online_names_now()
            if disabled:
                hidden_names = {name_of(k) for k in disabled}
                online_names = [n for n in online_names if n not in hidden_names]
            friends = [{"username": name_of(item), "online": name_of(item) in online_names} for item in friend_keys if item not in disabled]
            for item in friends:
                if welcomebot.is_bot(item["username"]):
                    item["bot"] = True    # 26.10.3: the app shows a "Helper bot" label
                    item["online"] = True
            friends.sort(key=lambda item: item["username"].lower())
            react_rev, react_map = visible_reactions(key, lobby)
            pins_out = pins_view(key, {g["id"]: g for g in groups_of(key) if g.get("id")})   # 26.10.13
            typing_out = typing_view(key, lobby, {g["id"]: g for g in groups_of(key) if g.get("id")})
            receipts_out = receipts_view(key, {g["id"]: g for g in groups_of(key) if g.get("id")})
        refresh = None
        remaining = accounts.session_expires_in(user)
        if remaining < 13 * 24 * 3600:
            refresh, exp = accounts.extend_session(user, accounts.SESSION_SECONDS)
            if refresh:
                remaining = max(0, int(exp - time.time()))
        body = {
            "ok": True,
            "me": {
                "username": user["username"],
                "role": user["role"],
                "owner": is_owner(user),
                "expires_in": remaining,
                "key_backup": key_backup_exists(user["username_key"]),
                "totp": accounts.totp_enabled(accounts.find_user(user["username_key"])),
            },
            # 0.23.3-beta.15: a pending "open the Administrator page" request from another device of the owner,
            # shown only to the owner's other, already-verified browsers so they can approve or deny it.
            "admin_approval": adminpage.approval_for(user, accounts.verify_cookie_ok(self.headers.get("Cookie"), user)) if is_owner(user) else None,
            "online": online_names,
            "friends": friends,
            "incoming": incoming,
            "outgoing": outgoing,
            "devices": device_out,
            "lobby": lobby_out,
            "groups": groups_out,
            "messages": visible,
            "more": more,
            "removed": removed,
            "gone": top_gone,
            "edited": edited,
            "edit": top_edit,
            "reactions_rev": react_rev,
            "typing": typing_out,
            "pins": pins_out,
            "receipts": receipts_out,
            "cv": cv_now,
        }
        if react_since != react_rev:
            body["reactions"] = react_map

        if publicmode.ANY:
            body["public"] = publicmode.sync_extra(self, user)
        try:
            body["mutes"] = mutes_of(user["username_key"])  # 0.23.2 per-chat mute, same on every device
        except Exception:
            body["mutes"] = {}
        try:
            body["chatlist"] = chatlist_of(user["username_key"])  # pins, order, archived; same on every device
        except Exception:
            body["chatlist"] = {"pins": [], "order": [], "archived": []}
        try:
            seen = {user["username"]}
            seen.update(item["username"] for item in friends)
            seen.update(item["username"] for item in incoming + outgoing)
            seen.update(item.get("username") for item in (lobby_out or {}).get("members", []) if isinstance(item, dict))
            for gv in groups_out:
                seen.update(item.get("username") for item in gv.get("members", []) if isinstance(item, dict))
            for msg in visible:
                if isinstance(msg, dict) and msg.get("event"):
                    seen.add(msg.get("actor"))
                    seen.add(msg.get("target"))
            for msg in visible:
                if isinstance(msg, dict):
                    seen.add(msg.get("sender"))
                    seen.add(msg.get("peer"))
            body["display_names"] = display_names_for(seen)
            body["name_colors"] = name_colors_for(seen)
            body["contacts"] = contacts_for(seen)
            body["nicknames"] = nicknames_for(user["username_key"], load_social_ro())  # 26.10.5: only your own
            body["avatars"] = avatars_for(seen)  # fix-avatar-refresh
        except Exception:
            body["contacts"] = {}
            body["display_names"] = {}
            body["name_colors"] = {}
        # 26.10.8 perf: delta sync. "st" stamps the people/keys/groups part of this answer; a browser that sends the
        # stamp it already has (st=...) gets those sections left out ("same": true) when nothing in them changed.
        # Browsers that send no st (26.10.7 and older pages, the service worker) always get the full answer.
        try:
            part = {k: body[k] for k in SYNC_DELTA_KEYS if k in body}
            stamp = hashlib.blake2s(json.dumps(part, separators=(",", ":"), sort_keys=True).encode("utf-8"), digest_size=8).hexdigest()
            body["st"] = stamp
            if (query.get("st") or [""])[0] == stamp:
                for k in part:
                    body.pop(k, None)
                body["same"] = True
        except Exception:
            pass
        self.send_json(200, body, cookie=refresh)

    def save_device(self, user):
        """Register this browser's public key. At most eight devices are kept. The oldest is dropped."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "This device could not be saved."})
            return
        device_id = (data.get("device_id") or "").lower()
        public_key = data.get("public_key") or ""
        if not HEX32.match(device_id) or not b64_ok(public_key, 80, 200):
            self.send_json(400, {"error": "This device could not be saved."})
            return
        key = user["username_key"]
        with _store_lock:
            rows = load_devices()
            for row in rows:
                if row.get("device_id") == device_id and row.get("user") != key:
                    self.send_json(400, {"error": "This device could not be saved."})
                    return
            kept = [row for row in rows if not (row.get("user") == key and row.get("device_id") == device_id)]
            mine = [row for row in kept if row.get("user") == key]
            if len(mine) >= 8:
                mine.sort(key=lambda row: row.get("updated") or 0)
                drop = mine[0].get("device_id")
                kept = [row for row in kept if row.get("device_id") != drop]
            kept.append({
                "user": key,
                "name": user["username"],
                "device_id": device_id,
                "public_key": public_key,
                "updated": int(time.time()),
            })
            save_devices(kept)
        self.send_json(200, {"ok": True})

    def invite(self, user):
        """Admin-only email invite. A failed send still keeps the invite and says the email did not send."""
        if user.get("role") != "admin":
            self.send_json(403, {"error": "Only an admin can invite people."})
            return
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        if self._invite_limited(user):
            return
        data = self.read_json_body() or {}
        role = data.get("role") or "member"
        token, error = accounts.create_invite(data.get("email") or "", role, invited_by=user["username_key"])
        if error:
            self.send_json(400, {"error": error})
            return
        self._invite_counted(user)
        link = directory.invite_link(PUBLIC_URL, token)
        mailed = accounts.send_invite(
            data.get("email").strip(),
            link,
            role,
            invited_by=user.get("username") or "",
            reply_to=user.get("email") or "",
        )
        if not mailed:
            self.send_json(200, {"ok": True, "mailed": False, "link": link, "error": "The invite is saved, but the email did not send."})
            return
        self.send_json(200, {"ok": True, "mailed": True, "link": link})

    def invite_resend(self, user):
        """Replace a pending invite's link and email it again. Members can resend their own; admins any."""
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        if self._invite_limited(user):
            return
        data = self.read_json_body() or {}
        email = (data.get("email") or "").strip()
        invite_id = data.get("id") if isinstance(data.get("id"), str) else ""
        token, role, error = accounts.resend_invite(email, user["username_key"], user.get("role") == "admin", invite_id)
        if error:
            self.send_json(400, {"error": error})
            return
        self._invite_counted(user)
        link = directory.invite_link(PUBLIC_URL, token)
        if not email:
            # Invite without email: an invite with no email gets a fresh link to share again; nothing is mailed.
            self.send_json(200, {"ok": True, "invited": True, "mailed": False, "link_only": True, "link": link})
            return
        mailed = accounts.send_invite(
            email,
            link,
            role,
            invited_by=user.get("username") or "",
            reply_to=user.get("email") or "",
        )
        if not mailed:
            self.send_json(200, {"ok": True, "invited": True, "mailed": False, "link": link, "error": "A new link is saved, but the email did not send."})
            return
        self.send_json(200, {"ok": True, "invited": True, "mailed": True, "link": link})

    def invite_link_only(self, user):
        """Invite without email: make a member invite with NO email and return its link at once, so the app can show the
        code and QR straight away. Nothing is emailed. The person who opens it enters their own email on the
        new-account page. Same rules as an emailed invite: 72 hours, works once, 30-pending cap, rate limits."""
        if accounts.pending_invite_count() >= 30:
            self.send_json(400, {"error": "Wait for an invite to be used before making another."})
            return
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        if self._invite_limited(user):
            return
        token, error = accounts.create_invite("", "member", invited_by=user["username_key"])
        if error or not token:
            self.send_json(400, {"error": error or "That invite was not made."})
            return
        self._invite_counted(user)
        link = directory.invite_link(PUBLIC_URL, token)
        self.send_json(200, {"ok": True, "invited": True, "mailed": False, "link_only": True, "link": link,
                             "hours": accounts.INVITE_SECONDS // 3600})

    def invites(self, user):
        """Pending invites (26.10.2: open to every signed-in user). Admins and the owner see everyone's, each
        member sees only the invites they sent. can_cancel is true on every row returned, because the list only
        holds invites this viewer may cancel; the app shows Cancel from it, and cancel_invite checks again."""
        boss = self._may_cancel_any_invite(user)
        rows = accounts.pending_invites(None if boss else user["username_key"])
        for row in rows:
            row["mine"] = row["invited_by"] == user["username_key"]
            row["can_cancel"] = True
            other = accounts.find_user(row["invited_by"]) if row["invited_by"] else None
            row["invited_by"] = (other.get("username") if other else row["invited_by"]) or ""
        self.send_json(200, {"ok": True, "invites": rows, "all": boss})

    def _may_cancel_any_invite(self, user):
        """26.10.2 rule: admins and the owner may cancel any pending invite; everyone else only their own."""
        return user.get("role") == "admin" or is_owner(user)

    def cancel_invite(self, user):
        """Cancel one pending invite by id (26.10.2: any signed-in user, for invites they are allowed to cancel).
        The server enforces the rule: someone else's invite is refused with 403 unless you are an admin or the owner.
        The link stops working at once, and who cancelled it is recorded and logged (usernames only, no email)."""
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        data = self.read_json_body() or {}
        invite_id = data.get("id") if isinstance(data.get("id"), str) else ""
        found, sender = accounts.invite_sender(invite_id)
        if not found:
            self.send_json(404, {"error": "That invite was not found. It may have been used or cancelled already."})
            return
        if sender != user["username_key"] and not self._may_cancel_any_invite(user):
            self.send_json(403, {"error": "Only the person who sent this invite, or an admin, can cancel it."})
            return
        error = accounts.cancel_invite(invite_id, by=user["username_key"])
        if error:
            self.send_json(400, {"error": error})
            return
        print("invite cancelled: id %s sent by %s, cancelled by %s" % (invite_id[:8], sender or "(unknown)", user["username_key"]), flush=True)
        self.send_json(200, {"ok": True})

    def friend_request(self, user):
        """Username adds a friend request. An unknown email creates a member invite from the sender."""
        data = self.read_json_body() or {}
        raw = (data.get("username") or "").strip()
        if not raw:
            self.send_json(400, {"error": "Enter a username or email."})
            return
        if "@" in raw:
            if not accounts.EMAIL_RE.match(raw) or len(raw) > 200:
                self.send_json(400, {"error": "Enter a real email address."})
                return
            other = accounts.find_user_by_email(raw)
            if not other:
                self._invite_friend(user, raw)
                return
        else:
            other = accounts.find_user(raw)
            if not other or not other.get("password") or other.get("disabled"):
                self.send_json(400, {"error": "No account uses that username. Use \"Invite friend or family to BranchChat\" in the menu to invite someone new."})
                return
        self._request_friend(user, other)

    def _invite_friend(self, user, email):
        """Save a member invite and try to email it from the inviting account."""
        if (user.get("email") or "").lower() == email.lower():
            self.send_json(400, {"error": "You cannot add yourself."})
            return
        if accounts.invite_pending(email):
            self.send_json(400, {"error": "An invite is already waiting for that email. Resend it to make a fresh link.", "pending": True, "email": email})
            return
        if accounts.pending_invite_count() >= 30:
            self.send_json(400, {"error": "Wait for an invite to be used before sending another."})
            return
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        if self._invite_limited(user):
            return
        token, error = accounts.create_invite(email, "member", invited_by=user["username_key"])
        if error or not token:
            self.send_json(400, {"error": error or "That invite was not sent."})
            return
        self._invite_counted(user)
        link = directory.invite_link(PUBLIC_URL, token)
        mailed = accounts.send_invite(
            email,
            link,
            "member",
            invited_by=user.get("username") or "",
            reply_to=user.get("email") or "",
        )
        if not mailed:
            self.send_json(200, {"ok": True, "invited": True, "mailed": False, "link": link, "error": "The invite is saved, but the email did not send."})
            return
        self.send_json(200, {"ok": True, "invited": True, "mailed": True, "link": link})

    def _request_friend(self, user, other):
        """Store a friend request between two existing accounts."""
        key = user["username_key"]
        other_key = other["username_key"]
        if other_key == key:
            self.send_json(400, {"error": "You cannot add yourself."})
            return
        if publicmode.blocked_either(key, other_key):
            self.send_json(400, {"error": "That request could not be sent."})
            return
        with _store_lock:
            social = load_social()
            if is_friend(key, other_key, social):
                self.send_json(400, {"error": "You are already friends."})
                return
            reverse = None
            for item in social["requests"]:
                if isinstance(item, dict) and item.get("from") == other_key and item.get("to") == key:
                    reverse = item
                if isinstance(item, dict) and item.get("from") == key and item.get("to") == other_key:
                    self.send_json(400, {"error": "That request is waiting."})
                    return
            outgoing = [item for item in social["requests"] if isinstance(item, dict) and item.get("from") == key]
            if reverse is None and len(outgoing) >= 20:
                self.send_json(400, {"error": "Wait for a pending request before sending another."})
                return
            became = False
            if reverse is not None:
                social["requests"] = [item for item in social["requests"] if item is not reverse]
                social["pairs"].append(sorted([key, other_key]))
                became = True
            else:
                social["requests"].append({"from": key, "to": other_key, "ts": int(time.time())})
            save_social(social)
        self.send_json(200, {"ok": True, "friends": became})

    def friend_accept(self, user):
        """Accept a request and create the friend pair."""
        self._friend_close(user, accept=True)

    def friend_decline(self, user):
        """Drop a request without creating a friendship."""
        self._friend_close(user, accept=False)

    def _friend_close(self, user, accept):
        """Remove one direction of a pending request."""
        data = self.read_json_body() or {}
        other = accounts.find_user(data.get("username") or "")
        if not other:
            self.send_json(400, {"error": "That request was not found."})
            return
        key = user["username_key"]
        other_key = other["username_key"]
        with _store_lock:
            social = load_social()
            match = None
            for item in social["requests"]:
                if isinstance(item, dict) and item.get("from") == other_key and item.get("to") == key:
                    match = item
                    break
            if match is None:
                self.send_json(400, {"error": "That request was not found."})
                return
            social["requests"] = [item for item in social["requests"] if item is not match]
            if accept and not is_friend(key, other_key, social):
                social["pairs"].append(sorted([key, other_key]))
            save_social(social)
        self.send_json(200, {"ok": True})

    def friend_remove(self, user):
        """Delete the friendship. Old direct messages stay on the devices that already have them."""
        data = self.read_json_body() or {}
        other = accounts.find_user(data.get("username") or "")
        if not other:
            self.send_json(400, {"error": "That friend was not found."})
            return
        key = user["username_key"]
        other_key = other["username_key"]
        with _store_lock:
            social = load_social()
            social["pairs"] = [
                pair for pair in social["pairs"]
                if not (isinstance(pair, list) and sorted(pair) == sorted([key, other_key]))
            ]
            social["requests"] = [
                item for item in social["requests"]
                if not (isinstance(item, dict) and {item.get("from"), item.get("to")} == {key, other_key})
            ]
            save_social(social)
        welcomebot.removed(key, other_key)  # 26.10.3: a removed Helper bot stays removed
        self.send_json(200, {"ok": True})

    def lobby_join(self, user):
        """Opt in. The epoch changes, so older lobby messages stay hidden from this member."""
        data = self.read_json_body() or {}
        key = user["username_key"]
        with _store_lock:
            lobby = load_lobby()
            devices = load_devices()
            if key in lobby["members"]:
                self.send_json(200, {"ok": True, "lobby": lobby_view(lobby, key, devices)})
                return
            if lobby["members"]:
                epoch = int(lobby.get("epoch") or 0) + 1
                if epoch <= 0:
                    epoch = 1
                lobby["epoch"] = epoch
                lobby["members"][key] = {"name": user["username"], "epoch_from": epoch, "since": int(time.time())}
                lobby["epochs"][str(epoch)] = []
                save_lobby(lobby)
                self.send_json(200, {"ok": True, "lobby": lobby_view(lobby, key, devices)})
                return
            wraps = take_wraps(data.get("wraps"), 32)
            if not wraps:
                self.send_json(400, {"error": "Open the lobby again so this device can make a room key."})
                return
            epoch = int(lobby.get("epoch") or 0) + 1
            if epoch <= 0:
                epoch = 1
            owned = False
            stored = []
            for wrap in wraps:
                owner = device_owner(devices, wrap["device_id"])
                if owner != key:
                    self.send_json(400, {"error": "Open the lobby again so this device can make a room key."})
                    return
                owned = True
                wrap["user"] = owner
                stored.append(wrap)
            if not owned:
                self.send_json(400, {"error": "Open the lobby again so this device can make a room key."})
                return
            lobby["epoch"] = epoch
            lobby["members"] = {key: {"name": user["username"], "epoch_from": epoch, "since": int(time.time())}}
            lobby["epochs"][str(epoch)] = stored
            save_lobby(lobby)
            self.send_json(200, {"ok": True, "epoch": epoch, "lobby": lobby_view(lobby, key, devices)})

    def lobby_leave(self, user):
        """Opt out and rotate the epoch. People who remain need a new room key."""
        key = user["username_key"]
        with _store_lock:
            lobby = load_lobby()
            if key not in lobby["members"]:
                self.send_json(200, {"ok": True})
                return
            lobby["members"].pop(key, None)
            lobby["epoch"] = int(lobby.get("epoch") or 0) + 1
            lobby["epochs"][str(lobby["epoch"])] = []
            if not lobby["members"]:
                lobby["epochs"][str(lobby["epoch"])] = []
            save_lobby(lobby)
        self.send_json(200, {"ok": True})

    def lobby_epoch(self, user):
        """First member to publish wraps for an empty epoch saves them. A later post is refused."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "The lobby key was not saved."})
            return
        key = user["username_key"]
        try:
            epoch = int(data.get("epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0
        wraps = take_wraps(data.get("wraps"), 64)
        if not wraps or epoch <= 0:
            self.send_json(400, {"error": "The lobby key was not saved."})
            return
        with _store_lock:
            lobby = load_lobby()
            devices = load_devices()
            if key not in lobby["members"] or epoch != int(lobby.get("epoch") or 0):
                self.send_json(400, {"error": "The lobby already has a key."})
                return
            current = lobby["epochs"].get(str(epoch), [])
            if isinstance(current, list) and current:
                self.send_json(400, {"error": "The lobby already has a key."})
                return
            stored = []
            mine = False
            member_keys = set(lobby["members"])
            for wrap in wraps:
                owner = device_owner(devices, wrap["device_id"])
                if owner not in member_keys:
                    continue
                if owner == key:
                    mine = True
                wrap["user"] = owner
                stored.append(wrap)
            if not mine or not stored:
                self.send_json(400, {"error": "The lobby key was not saved."})
                return
            lobby["epochs"][str(epoch)] = stored
            save_lobby(lobby)
        self.send_json(200, {"ok": True})

    def lobby_wraps(self, user):
        """A member who already holds the epoch key wraps it for devices that joined later."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That room key was not shared."})
            return
        key = user["username_key"]
        try:
            epoch = int(data.get("epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0
        wraps = take_wraps(data.get("wraps"), 64)
        if not wraps or epoch <= 0:
            self.send_json(400, {"error": "That room key was not shared."})
            return
        with _store_lock:
            lobby = load_lobby()
            devices = load_devices()
            if key not in lobby["members"]:
                self.send_json(403, {"error": "Join the lobby first."})
                return
            current = lobby["epochs"].get(str(epoch), [])
            if not isinstance(current, list) or not current:
                self.send_json(400, {"error": "The lobby does not have that key yet."})
                return
            my_ids = {row.get("device_id") for row in devices if row.get("user") == key}
            if not any(isinstance(wrap, dict) and wrap.get("user") == key and wrap.get("device_id") in my_ids for wrap in current):
                self.send_json(403, {"error": "Wait for a member to share the room key with you."})
                return
            member_keys = set(lobby["members"])
            have = {(wrap.get("user"), wrap.get("device_id")) for wrap in current if isinstance(wrap, dict)}
            for wrap in wraps:
                owner = device_owner(devices, wrap["device_id"])
                if owner not in member_keys:
                    continue
                mark = (owner, wrap["device_id"])
                if mark in have:
                    continue
                wrap["user"] = owner
                current.append(wrap)
                have.add(mark)
            lobby["epochs"][str(epoch)] = current
            save_lobby(lobby)
        self.send_json(200, {"ok": True})

    def push_key(self, user):
        """Public half of the push key. The private half stays on the server and is not printed."""
        try:
            public = webpush.public_key()
        except Exception:
            self.send_json(500, {"error": "Phone alerts are not available right now."})
            return
        self.send_json(200, {"publicKey": public})

    def push_subscribe(self, user):
        """Store a browser push subscription. The endpoint is not written into logs."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "This phone could not be registered for alerts."})
            return
        try:
            until = int(data.get("paused_until") or 0)
        except (TypeError, ValueError):
            until = -1
        saved = webpush.save_subscription(
            user["username_key"],
            data.get("endpoint"),
            data.get("p256dh"),
            data.get("auth"),
            until,
        )
        if not saved:
            self.send_json(400, {"error": "This phone could not be registered for alerts."})
            return
        self.send_json(200, {"ok": True})

    def push_unsubscribe(self, user):
        """Forget one push endpoint for this account."""
        data = self.read_json_body() or {}
        endpoint = data.get("endpoint") if isinstance(data.get("endpoint"), str) else ""
        webpush.drop_endpoint(user["username_key"], endpoint)
        self.send_json(200, {"ok": True})

    def chat_mute(self, user):
        """Mute or unmute one chat for this account on every device. {chat, for: 1h|8h|1w|forever|off}."""
        data = self.read_json_body() or {}
        key = norm_mute_key(data.get("chat"))
        choice = data.get("for")
        if not key or (choice != "off" and choice not in MUTE_FOR):
            self.send_json(400, {"error": "That chat could not be muted."})
            return
        now = int(time.time())
        with _store_lock:
            store = load_mutes()
            row = mutes_of(user["username_key"], store, now)
            if choice == "off":
                row.pop(key, None)
            else:
                if key not in row and len(row) >= MUTE_MAX:
                    self.send_json(400, {"error": "Too many muted chats. Unmute one first."})
                    return
                row[key] = (now + MUTE_FOR[choice]) if MUTE_FOR[choice] else 0
            if row:
                store[user["username_key"]] = row
            else:
                store.pop(user["username_key"], None)
            atomic_write(MUTES_PATH, json.dumps(store))
        self.send_json(200, {"ok": True, "chat": key, "until": row.get(key), "muted": key in row, "mutes": row, "now": now})

    def chat_list(self, user):
        """Pin, unpin, archive, unarchive or reorder chats for this account on every device.
        {op: pin|unpin|archive|unarchive, chat} or {op: order, pins?: [keys], order?: [keys]}."""
        data = self.read_json_body() or {}
        op = data.get("op")
        chat = norm_mute_key(data.get("chat"))
        if op not in ("pin", "unpin", "archive", "unarchive", "order") or (op != "order" and not chat):
            self.send_json(400, {"error": "That chat could not be updated."})
            return
        user_key = user["username_key"]
        with _store_lock:
            store = read_json(CHATLIST_PATH, {})
            row = chatlist_of(user_key, store)
            drop = lambda lst: [k for k in lst if k != chat]
            if op == "pin":
                row["pins"] = [chat] + drop(row["pins"])
                row["archived"] = drop(row["archived"])
            elif op == "unpin":
                row["pins"] = drop(row["pins"])
            elif op == "archive":
                row["pins"] = drop(row["pins"])
                if chat not in row["archived"]:
                    row["archived"].append(chat)
            elif op == "unarchive":
                row["archived"] = drop(row["archived"])
            else:
                if "pins" in data:
                    # Only reorders chats that are already pinned; pinned chats missing from the list keep their place at the end.
                    want = [k for k in _chatlist_clean(data.get("pins")) if k in row["pins"]]
                    row["pins"] = want + [k for k in row["pins"] if k not in want]
                if "order" in data:
                    row["order"] = _chatlist_clean(data.get("order"))
            row = {k: v[:CHATLIST_MAX] for k, v in row.items()}
            if any(row.values()):
                store[user_key] = row
            else:
                store.pop(user_key, None)
            atomic_write(CHATLIST_PATH, json.dumps(store))
        self.send_json(200, {"ok": True, "chatlist": row})

    def _push_targets(self, row, sender_key):
        """Who should be woken. The sender is not woken for their own post. The body is only a wake-up."""
        if row.get("box") == "dm":
            other = row.get("b") if row.get("a") == sender_key else row.get("a")
            if other and other != sender_key:
                return publicmode.drop_blockers([other], sender_key)
            return []
        if row.get("box") == "lobby":
            lobby = load_lobby()
            return publicmode.drop_blockers([item for item in lobby.get("members", {}) if item != sender_key], sender_key)
        if row.get("box") == "group":
            g = live_group(row.get("g"))
            # 26.10.2: like DMs and the Lobby, members who blocked the sender are not woken by them.
            return publicmode.drop_blockers([item for item in (g["members"] if g else {}) if item != sender_key], sender_key)
        return []

    def post_message(self, user):
        """Store ciphertext for a direct message or the lobby. The server does not decrypt it."""
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That message was not sent."})
            return
        box = data.get("box")
        iv = data.get("iv") or ""
        ct = data.get("ct") or ""
        if box not in ("dm", "lobby", "group") or not b64_ok(iv, 8, 64) or not b64_ok(ct, 16, TEXT_CT_MAX):
            self.send_json(400, {"error": "That message was not sent."})
            return
        key = user["username_key"]
        filtered = None
        if box == "lobby" and publicmode.LOBBY_CHECK:
            # Public server only: open, filter and re-seal the Lobby message (see publicmode.lobby_check).
            new_iv, new_ct, info = publicmode.lobby_check(self, user, data)
            if new_iv is None:
                self.send_json(info[0], {"error": info[1]})
                return
            data["iv"], data["ct"] = new_iv, new_ct
            data.pop("ck", None)
            filtered = info
        with _store_lock:
            if box == "dm":
                row, error = self._dm_row(user, data)
            elif box == "group":
                row, error = self._group_row(user, data)
            else:
                row, error = self._lobby_row(user, data)
            if error:
                code = 403 if error.startswith("You can") or error.startswith("Join") or error.startswith("You are not") else 400
                if error == GROUP_REKEY:
                    code = 409
                self.send_json(code, {"error": error})
                return
            row["seq"] = (_messages[-1]["seq"] + 1) if _messages else 1
            row["id"] = secrets.token_hex(16)
            row["sender"] = key
            row["sender_name"] = user["username"]
            row["ts"] = int(time.time())
            blob = row.get("blob")
            if blob:
                meta_path = os.path.join(BLOB_DIR, blob["id"], "meta.json")
                meta = read_json(meta_path, {})
                meta["message"] = row["id"]
                atomic_write(meta_path, json.dumps(meta))
            append_message(row)
            try:
                typing_clear_user("dm:" + row["a"] + ":" + row["b"] if box == "dm" else "group:" + (row.get("g") or "") if box == "group" else "lobby", key)
            except Exception:
                pass
            public = message_for(row, key)
            targets = self._push_targets(row, key)
            if targets:
                # 0.23.2: nobody is woken for a chat they muted (unread counts still come with the next sync).
                ck = push_chat_key(row, user["username"])
                mutes = load_mutes()
                if ck and mutes:
                    kept = [t for t in targets if not chat_muted(t, ck, mutes)]
                    if len(kept) != len(targets):
                        webpush._log("muted-skip %d" % (len(targets) - len(kept)))
                    targets = kept
        try:
            # 26.10.3 Helper bot: answer messages sent to it, and tick off tour steps.
            # 26.10.5: the app says whether this person sees the phone or computer layout, so the bot's directions match.
            welcomebot.message(user, row, view=data.get("view") if box == "dm" else None)
            if not (box == "dm" and welcomebot.is_bot(data.get("peer") or "")):
                welcomebot.step(key, "first_message")
            if box == "lobby":
                welcomebot.step(key, "lobby")
        except Exception:
            pass
        reply = {"ok": True, "message": public}
        if filtered is not None:
            reply["filtered"] = publicmode.lobby_filtered(user, filtered, publicmode.is_moderator(self, user), row["id"])
        self.send_json(200, reply)
        try:
            webpush.notify(targets)
        except Exception:
            pass

    def react_message(self, user):
        """Add or remove one reaction emoji. Five different emoji is the room cap. Deleted messages refuse this."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That reaction was not saved."})
            return
        mid = (data.get("id") or "").lower()
        emoji = data.get("emoji") if isinstance(data.get("emoji"), str) else ""
        if not HEX32.match(mid) or not emoji_ok(emoji):
            self.send_json(400, {"error": "That reaction was not saved."})
            return
        key = user["username_key"]
        with _store_lock:
            msg = _by_id.get(mid)
            lobby = load_lobby()
            if not msg or msg.get("deleted") or msg.get("event") or not can_see(msg, key, lobby):
                self.send_json(404, {"error": "That message was not found."})
                return
            store = load_reactions()
            rows = [row for row in (store["items"].get(mid) or []) if isinstance(row, dict)]
            already = any(row.get("user") == key and row.get("emoji") == emoji for row in rows)
            if already:
                rows = [row for row in rows if not (row.get("user") == key and row.get("emoji") == emoji)]
            else:
                distinct = []
                for row in rows:
                    mark = row.get("emoji")
                    if mark not in distinct:
                        distinct.append(mark)
                if emoji not in distinct and len(distinct) >= REACT_CAP:
                    self.send_json(400, {"error": "This message already has five reactions."})
                    return
                rows.append({"emoji": emoji, "user": key})
            if rows:
                store["items"][mid] = rows
            else:
                store["items"].pop(mid, None)
            store["rev"] = int(store["rev"]) + 1
            save_reactions(store)
            public = public_reactions(rows, key)
            rev = int(store["rev"])
        self.send_json(200, {"ok": True, "id": mid, "reactions": public, "reactions_rev": rev})

    def delete_message(self, user):
        """Drop one message for everyone who can see that conversation.

        The sender may remove their own lobby or direct message. An admin
        may remove any message they can see, including someone else's.
        The row stays so clients can show that it was removed. Ciphertext
        and the encrypted file are erased. Assumes the caller is already
        signed in and the id is the 32-hex message id.
        """
        data = self.read_json_body() or {}
        mid = (data.get("id") or "").lower()
        if not HEX32.match(mid):
            self.send_json(400, {"error": "That message was not removed."})
            return
        key = user["username_key"]
        admin = user.get("role") == "admin"
        blob_id = ""
        with _store_lock:
            msg = _by_id.get(mid)
            lobby = load_lobby()
            if not msg or msg.get("event") or not can_see(msg, key, lobby):
                self.send_json(404, {"error": "That message was not found."})
                return
            # 0.23.0: a group's owner and admins may remove any message in that group.
            group_mod = msg.get("box") == "group" and group_role(live_group(msg.get("g")), key) in ("owner", "admin")
            if msg.get("sender") != key and not admin and not group_mod:
                self.send_json(403, {"error": "You can remove your own messages."})
                return
            if not msg.get("deleted"):
                blob = msg.get("blob") if isinstance(msg.get("blob"), dict) else None
                if blob and HEX32.match(blob.get("id") or ""):
                    blob_id = blob["id"]
                msg["deleted"] = True
                msg["deleted_seq"] = next_deleted_seq()
                # 26.10.2: remember which delete animation the deleter saw, so other devices play it too.
                anim, seed = delete_anim_from(data)
                if anim:
                    msg["del_anim"] = anim
                    if seed is not None:
                        msg["del_seed"] = seed
                for field in ("iv", "ct", "wraps", "epoch_iv", "epoch_ct", "blob"):
                    msg.pop(field, None)
                self._rewrite_messages(msg)
                if blob_id:
                    _blob_seq.pop(blob_id, None)
                store = load_reactions()
                if mid in store["items"]:
                    store["items"].pop(mid, None)
                    store["rev"] = int(store["rev"]) + 1
                    save_reactions(store)
        if blob_id:
            shutil.rmtree(os.path.join(BLOB_DIR, blob_id), ignore_errors=True)
        self.send_json(200, {"ok": True, "id": mid})

    def edit_message(self, user):
        """Store new ciphertext for one message. The browser encrypted it. seq stays put.

        The sender may change one of their three newest live messages in that
        lobby or direct chat. An admin may change any live message they can
        see. The attached file stays. Tombstones cannot be edited.
        """
        data = self.read_json_body() or {}
        if too_fast(user["username_key"]):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        mid = (data.get("id") or "").lower()
        iv = data.get("iv") or ""
        ct = data.get("ct") or ""
        if not HEX32.match(mid) or not b64_ok(iv, 8, 64) or not b64_ok(ct, 16, TEXT_CT_MAX):
            self.send_json(400, {"error": "That message was not changed."})
            return
        key = user["username_key"]
        admin = user.get("role") == "admin"
        filtered = None
        if publicmode.LOBBY_CHECK:
            peek = _by_id.get(mid)
            if peek and peek.get("box") == "lobby":
                new_iv, new_ct, info = publicmode.lobby_check(self, user, data, edit=True)
                if new_iv is None:
                    self.send_json(info[0], {"error": info[1]})
                    return
                iv, ct, filtered = new_iv, new_ct, info
        with _store_lock:
            msg = _by_id.get(mid)
            lobby = load_lobby()
            if not msg or msg.get("deleted") or msg.get("event") or not can_see(msg, key, lobby):
                self.send_json(404, {"error": "That message was not found."})
                return
            if not admin and not sender_may_edit(msg, key):
                self.send_json(403, {"error": "You can edit your last three messages."})
                return
            if publicmode.LOBBY_CHECK and msg.get("box") == "lobby" and filtered is None:
                self.send_json(400, {"error": "That message was not changed."})
                return
            msg["iv"] = iv
            msg["ct"] = ct
            msg["edited_ts"] = int(time.time())
            msg["edited_seq"] = next_edited_seq()
            self._rewrite_messages(msg)
            public = message_for(msg, key)
        reply = {"ok": True, "message": public}
        if filtered is not None:
            reply["filtered"] = publicmode.lobby_filtered(user, filtered, publicmode.is_moderator(self, user), mid)
        self.send_json(200, reply)

    def contact_put(self, user):
        """Save the phone number and the two opt-in display flags. Both flags default to off."""
        data = self.read_json_body()
        if not isinstance(data, dict):
            self.send_json(400, {"error": "That was not saved."})
            return
        phone = clean_phone(data.get("phone", ""))
        if phone is None:
            self.send_json(400, {"error": "Phone numbers use digits, spaces, + - ( ) . and 7 to 20 digits."})
            return
        row = {"show_email": data.get("show_email") is True, "phone": phone, "show_phone": data.get("show_phone") is True}
        set_contact(user["username_key"], row)
        self.send_json(200, dict(row, ok=True))

    def name_color_put(self, user):
        """Save the display name color. Empty resets to the default."""
        data = self.read_json_body()
        value = clean_color(data.get("color")) if isinstance(data, dict) else None
        if value is None:
            self.send_json(400, {"error": "That color is not allowed."})
            return
        set_name_color(user["username_key"], value)
        self.send_json(200, {"ok": True, "color": value})

    def display_name_put(self, user):
        """Save the optional display name. Empty clears it. This is not message content."""
        data = self.read_json_body()
        value = clean_display((data or {}).get("display_name", None)) if isinstance(data, dict) else None
        if value is None:
            self.send_json(400, {"error": "Display names can be up to 32 characters."})
            return
        refused = publicmode.name_refused(value, "display name", user, publicmode.is_moderator(self, user))
        if refused:
            self.send_json(400, {"error": refused})
            return
        set_display_name(user["username_key"], value)
        self.send_json(200, {"ok": True, "display_name": value})

    def avatar_get(self, user, versioned=False):
        """Return this account's picture. No picture is a 404 so the page can use the default smiley."""
        path = avatar_path(user["username_key"])
        if not path:
            self.send_json(404, {"error": "No profile picture is set."})
            return
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json(404, {"error": "No profile picture is set."})
            return
        if not jpeg_ok(body):
            self.send_json(404, {"error": "No profile picture is set."})
            return
        # fix-avatar-refresh: a ?v= URL names one exact picture, so it may be cached; a new picture gets a new URL.
        self.send_bytes(200, body, "image/jpeg", [
            ("Cache-Control", "private, max-age=31536000, immutable" if versioned else "private, no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ])

    def avatar_of(self, user):
        """Another account's picture for the sidebar. Signed-in users only. No picture is a 404."""
        name = (parse_qs(urlparse(self.path).query).get("u") or [""])[0]
        other = accounts.find_user(name) if name else None
        if not other:
            self.send_json(404, {"error": "No profile picture is set."})
            return
        ver = (parse_qs(urlparse(self.path).query).get("v") or [""])[0]
        self.avatar_get(other, versioned=bool(ver) and ver == str(avatar_version(other["username_key"])))

    def avatar_put(self, user):
        """Store a JPEG the browser already cropped. The server can see this picture. It is not message content."""
        path = avatar_path(user["username_key"])
        length = getattr(self, "_unread", -1)
        if not path or length < 32 or length > AVATAR_MAX:
            self.send_json(400, {"error": "That picture was not saved."})
            return
        raw = self._read_exact(length)
        if not jpeg_ok(raw):
            self.send_json(400, {"error": "That picture was not saved."})
            return
        os.makedirs(AVATAR_DIR, exist_ok=True)
        os.chmod(AVATAR_DIR, 0o700)
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except OSError:
            self.send_json(400, {"error": "That picture was not saved."})
            return
        bump_change()  # fix-avatar-refresh: wake every /api/wait so friends' pages pick up the new version now
        self.send_json(200, {"ok": True, "v": avatar_version(user["username_key"])})

    def _dm_row(self, user, data):
        """Build a direct-message row. Both people must be friends, and every wrap must belong to one of them."""
        other = accounts.find_user(data.get("peer") or "")
        if not other or not other.get("password"):
            return None, "You can only message a friend."
        key = user["username_key"]
        other_key = other["username_key"]
        social = load_social()
        # 26.10.13 My Stash: a DM to yourself. Every wrap must then be one of YOUR devices (checked below), so only you can read it.
        if other_key != key and (not is_friend(key, other_key, social) or publicmode.blocked_either(key, other_key)):
            return None, "You can only message a friend."
        wraps = take_wraps(data.get("wraps"), 16)
        if not wraps:
            return None, "That message was not sent."
        devices = load_devices()
        allowed = {key, other_key}
        clean = []
        for wrap in wraps:
            owner = device_owner(devices, wrap["device_id"])
            if owner not in allowed:
                return None, "That message was not sent."
            wrap["user"] = owner
            clean.append(wrap)
        blob, blob_error = self._claim_blob(data.get("blob"), key)
        if blob_error:
            return None, blob_error
        pair = sorted([key, other_key])
        return {
            "box": "dm",
            "a": pair[0],
            "b": pair[1],
            "epoch": 0,
            "iv": data["iv"],
            "ct": data["ct"],
            "wraps": clean,
            "epoch_iv": "",
            "epoch_ct": "",
            "blob": blob,
        }, ""

    def _lobby_row(self, user, data):
        """Build a lobby row sealed to the current epoch. The sender must already be a member."""
        key = user["username_key"]
        lobby = load_lobby()
        if key not in lobby["members"]:
            return None, "Join the lobby before sending there."
        epoch = int(lobby.get("epoch") or 0)
        try:
            asked = int(data.get("epoch") or 0)
        except (TypeError, ValueError):
            asked = 0
        current = lobby.get("epochs", {}).get(str(epoch), [])
        if asked != epoch or not isinstance(current, list) or not current:
            return None, "The lobby is getting a new key. Wait a moment and try again."
        if not b64_ok(data.get("epoch_iv") or "", 8, 64) or not b64_ok(data.get("epoch_ct") or "", 16, 400):
            return None, "That message was not sent."
        blob, blob_error = self._claim_blob(data.get("blob"), key)
        if blob_error:
            return None, blob_error
        return {
            "box": "lobby",
            "a": "",
            "b": "",
            "epoch": epoch,
            "iv": data["iv"],
            "ct": data["ct"],
            "wraps": [],
            "epoch_iv": data["epoch_iv"],
            "epoch_ct": data["epoch_ct"],
            "blob": blob,
        }, ""

    def _group_row(self, user, data):
        """0.23.0: a group row sealed to the group's current epoch. The sender must be a member."""
        key = user["username_key"]
        g = live_group(data.get("g"))
        if not g or key not in g["members"]:
            return None, "You are not in that group."
        epoch = int(g["epoch"])
        try:
            asked = int(data.get("epoch") or 0)
        except (TypeError, ValueError):
            asked = 0
        current = g["epochs"].get(str(epoch))
        if asked != epoch or not isinstance(current, list) or not current:
            return None, GROUP_REKEY
        if not b64_ok(data.get("epoch_iv") or "", 8, 64) or not b64_ok(data.get("epoch_ct") or "", 16, 400):
            return None, "That message was not sent."
        blob, blob_error = self._claim_blob(data.get("blob"), key)
        if blob_error:
            return None, blob_error
        return {
            "box": "group", "g": g["id"], "a": "", "b": "", "epoch": epoch,
            "iv": data["iv"], "ct": data["ct"], "wraps": [],
            "epoch_iv": data["epoch_iv"], "epoch_ct": data["epoch_ct"], "blob": blob,
        }, ""

    def _group_meta(self, data):
        """Encrypted group name (and later the picture key). The server only checks the shape."""
        meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict):
            return None
        iv = meta.get("iv") or ""
        ct = meta.get("ct") or ""
        if not b64_ok(iv, 8, 64) or not b64_ok(ct, 16, GROUP_META_MAX):
            return None
        return {"iv": iv, "ct": ct}

    def _group_wraps(self, raw_wraps, devices, allowed, me_key, limit=GROUP_WRAP_MAX):
        """Epoch-key wraps: every device must belong to an allowed account, and one must be the caller's."""
        wraps = take_wraps(raw_wraps, limit)
        if not wraps:
            return None
        clean = []
        mine = False
        for wrap in wraps:
            owner = device_owner(devices, wrap["device_id"])
            if not owner:
                continue  # a device dropped since the browser last synced
            if owner not in allowed:
                return None
            if owner == me_key:
                mine = True
            wrap["user"] = owner
            clean.append(wrap)
        return clean if (mine and clean) else None

    def _group_people(self, user, names, social, current, gid=""):
        """Resolve usernames to friends of the caller who are not in the group yet. Returns (keys, error).
        26.10.2: gid is the group being added to ("" while creating). Where report/block is on, nobody can be
        put into a group they left and blocked, and people who blocked each other can't add one another."""
        key = user["username_key"]
        if not isinstance(names, list) or not names or len(names) > GROUP_MAX:
            return None, "Pick at least one friend."
        disabled = accounts.disabled_keys()
        out = {}
        for name in names:
            other = accounts.find_user(name) if isinstance(name, str) and 0 < len(name) <= 80 else None
            if not other or not other.get("password"):
                return None, "You can only add friends to a group."
            okey = other["username_key"]
            if okey == key or okey in current:
                continue
            if welcomebot.is_bot(okey) or other.get("bot"):
                # 26.10.3: the Helper bot is a one-to-one helper only, never a group member.
                return None, "The Helper bot only chats one-to-one, so it can't be added to a group."
            if okey in disabled or not is_friend(key, okey, social):
                return None, "You can only add friends to a group."
            if publicmode.group_add_refused(key, okey, gid):
                # Same words for both cases, so the adder doesn't learn that they were blocked.
                return None, "%s can't be added to this group." % other["username"]
            out[okey] = other["username"]
        if not out:
            return None, "Those people are already in the group."
        return out, ""

    # Group requests: read the body and the group it names; replies 404 unless the caller is a member.
    def _group_body(self, user):
        data = self.read_json_body()
        if not isinstance(data, dict):
            self.send_json(400, {"error": "That did not work."})
            return None, None
        g = live_group(data.get("id"))
        if not g or user["username_key"] not in g["members"]:
            self.send_json(404, {"error": "That group was not found."})
            return None, None
        return data, g

    # The caller's view of a group (members, sealed name, their key wraps) for a reply.
    def _group_reply(self, g, key):
        devices = load_devices()
        return group_view(g, key, devices, set(online_names_now()), accounts.disabled_keys())

    def group_create(self, user):
        """Make a group of the caller and some friends. The browser made epoch 1 and wrapped it to everyone."""
        key = user["username_key"]
        if group_too_fast(key):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        data = self.read_json_body()
        if not isinstance(data, dict):
            self.send_json(400, {"error": "That group was not made."})
            return
        with _store_lock:
            social = load_social()
            devices = load_devices()
            people, error = self._group_people(user, data.get("members"), social, {})
            if error:
                self.send_json(400, {"error": error})
                return
            if len(people) + 1 > GROUP_MAX:
                self.send_json(400, {"error": "A group can have up to %d people." % GROUP_MAX})
                return
            if len(groups_of(key)) >= GROUP_PER_USER:
                self.send_json(400, {"error": "You are in %d groups already. Leave one first." % GROUP_PER_USER})
                return
            meta = self._group_meta(data)
            wraps = self._group_wraps(data.get("wraps"), devices, set(people) | {key}, key)
            if not meta or not wraps:
                self.send_json(400, {"error": "That group was not made. Refresh and try again."})
                return
            now = int(time.time())
            gid = secrets.token_hex(16)
            members = {key: {"name": user["username"], "role": "owner", "epoch_from": 1, "since": now, "added_by": key}}
            for okey, oname in people.items():
                members[okey] = {"name": oname, "role": "member", "epoch_from": 1, "since": now, "added_by": key}
            g = {
                "id": gid, "created": now, "creator": key, "epoch": 1, "history": "join",
                "settings": {"members_can_add": False, "members_can_edit_info": True},
                "members": members, "epochs": {"1": wraps},
                "meta": {"epoch": 1, "iv": meta["iv"], "ct": meta["ct"], "rev": 1}, "deleted": False,
            }
            _groups[gid] = g
            save_group(g)
            group_event(g, "create", key)
            view = self._group_reply(g, key)
        self.send_json(200, {"ok": True, "group": view})

    # Before a key change: refuse (409) when the browser worked from an old epoch or name revision, so it retries.
    def _group_rotate_check(self, g, data):
        try:
            from_epoch = int(data.get("from_epoch") or 0)
        except (TypeError, ValueError):
            from_epoch = 0
        if from_epoch != int(g["epoch"]) or not self._group_rev_ok(g, data):
            self.send_json(409, {"error": "The group just changed. Trying again.", "stale": True})
            return False
        return True

    def _group_rev_ok(self, g, data):
        """0.23.4: if the browser says which name revision it re-sealed, it must be the current one (else a rename is lost).
        Older browsers don't send meta_rev and are not checked."""
        if "meta_rev" not in data:
            return True
        try:
            return int(data.get("meta_rev")) == int((g.get("meta") or {}).get("rev") or 0)
        except (TypeError, ValueError):
            return False

    def group_rename(self, user):
        """0.23.4: rename a group. The browser seals the new name with the current group key (meta) and once more for the
        notice row (note). Who may: owner or admin, or any member while settings.members_can_edit_info is on (the default)."""
        key = user["username_key"]
        if group_too_fast(key):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            role = group_role(g, key)
            if role != "owner":   # 26.10.13 (Luke): group settings are owner-only
                self.send_json(403, {"error": "Only the group owner can change this group."})
                return
            try:
                asked = int(data.get("epoch") or 0)
            except (TypeError, ValueError):
                asked = 0
            current = g["epochs"].get(str(int(g["epoch"])))
            if asked != int(g["epoch"]) or not isinstance(current, list) or not current:
                self.send_json(409, {"error": GROUP_REKEY, "stale": True})
                return
            if "rev" in data:
                try:
                    rev_ok = int(data.get("rev")) == int((g.get("meta") or {}).get("rev") or 0)
                except (TypeError, ValueError):
                    rev_ok = False
                if not rev_ok:
                    self.send_json(409, {"error": "The group just changed. Trying again.", "stale": True})
                    return
            meta = self._group_meta(data)
            note = self._group_meta({"meta": data.get("note")})
            if not meta or not note:
                self.send_json(400, {"error": "The group was not renamed. Refresh and try again."})
                return
            epoch = int(g["epoch"])
            g["meta"] = {"epoch": epoch, "iv": meta["iv"], "ct": meta["ct"], "rev": int((g.get("meta") or {}).get("rev") or 0) + 1}
            save_group(g)
            group_event(g, "rename", key, "", {"name_iv": note["iv"], "name_ct": note["ct"]})
            view = self._group_reply(g, key)
        self.send_json(200, {"ok": True, "group": view})

    def group_add(self, user):
        """Owner or admin adds friends. The browser sends the next epoch key, wrapped for everyone in the new member set."""
        key = user["username_key"]
        if group_too_fast(key):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            role = group_role(g, key)
            if role != "owner":   # 26.10.13: owner-only
                self.send_json(403, {"error": "Only the group owner can add people."})
                return
            if not self._group_rotate_check(g, data):
                return
            social = load_social()
            devices = load_devices()
            people, error = self._group_people(user, data.get("usernames"), social, g["members"], g["id"])
            if error:
                self.send_json(400, {"error": error})
                return
            if len(g["members"]) + len(people) > GROUP_MAX:
                self.send_json(400, {"error": "A group can have up to %d people." % GROUP_MAX})
                return
            meta = self._group_meta(data)
            wraps = self._group_wraps(data.get("wraps"), devices, set(g["members"]) | set(people), key)
            if not meta or not wraps:
                self.send_json(400, {"error": "Nobody was added. Refresh and try again."})
                return
            new_epoch = int(g["epoch"]) + 1
            now = int(time.time())
            for okey, oname in people.items():
                g["members"][okey] = {"name": oname, "role": "member", "epoch_from": new_epoch, "since": now, "added_by": key}
            g["epoch"] = new_epoch
            g["epochs"][str(new_epoch)] = wraps
            g["meta"] = {"epoch": new_epoch, "iv": meta["iv"], "ct": meta["ct"], "rev": int((g.get("meta") or {}).get("rev") or 0) + 1}
            save_group(g)
            for okey in people:
                group_event(g, "add", key, okey)
            view = self._group_reply(g, key)
        self.send_json(200, {"ok": True, "group": view})

    def group_remove(self, user):
        """Owner or admin removes someone. The next epoch key is wrapped only for the people who stay."""
        key = user["username_key"]
        if group_too_fast(key):
            self.send_json(429, {"error": "Wait a second and try again."})
            return
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            role = group_role(g, key)
            other = accounts.find_user(data.get("username") or "") if isinstance(data.get("username"), str) else None
            okey = other["username_key"] if other else ""
            if not okey or okey not in g["members"]:
                self.send_json(404, {"error": "That person is not in this group."})
                return
            if okey == key:
                self.send_json(400, {"error": "Use Leave group to leave."})
                return
            target = group_role(g, okey)
            if role != "owner" or target == "owner":   # 26.10.13: owner-only
                self.send_json(403, {"error": "You can't remove that person."})
                return
            if not self._group_rotate_check(g, data):
                return
            devices = load_devices()
            stay = set(g["members"]) - {okey}
            meta = self._group_meta(data)
            wraps = self._group_wraps(data.get("wraps"), devices, stay, key)
            if not meta or not wraps:
                self.send_json(400, {"error": "Nobody was removed. Refresh and try again."})
                return
            g["members"].pop(okey, None)
            group_drop_wraps(g, okey)
            new_epoch = int(g["epoch"]) + 1
            g["epoch"] = new_epoch
            g["epochs"][str(new_epoch)] = wraps
            g["meta"] = {"epoch": new_epoch, "iv": meta["iv"], "ct": meta["ct"], "rev": int((g.get("meta") or {}).get("rev") or 0) + 1}
            save_group(g)
            group_event(g, "remove", key, okey)
            view = self._group_reply(g, key)
        self.send_json(200, {"ok": True, "group": view})

    def group_leave_api(self, user):
        """Leave a group. The remaining members get a new key from whoever is online next."""
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            group_leave(g, user["username_key"])
        self.send_json(200, {"ok": True})

    def group_epoch(self, user):
        """Fill an empty epoch (after someone left). The first member to post wins, like the lobby."""
        key = user["username_key"]
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            try:
                epoch = int(data.get("epoch") or 0)
            except (TypeError, ValueError):
                epoch = 0
            current = g["epochs"].get(str(epoch))
            if epoch != int(g["epoch"]) or (isinstance(current, list) and current):
                self.send_json(400, {"error": "The group already has a key."})
                return
            devices = load_devices()
            wraps = self._group_wraps(data.get("wraps"), devices, set(g["members"]), key)
            if not wraps:
                self.send_json(400, {"error": "The group key was not saved."})
                return
            g["epochs"][str(epoch)] = wraps
            meta = self._group_meta(data)
            if meta and self._group_rev_ok(g, data):
                g["meta"] = {"epoch": epoch, "iv": meta["iv"], "ct": meta["ct"], "rev": int((g.get("meta") or {}).get("rev") or 0) + 1}
            save_group(g)
        self.send_json(200, {"ok": True})

    def group_wraps(self, user):
        """A member device that holds an epoch key wraps it for member devices that do not have it yet."""
        key = user["username_key"]
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            items = data.get("items")
            if not isinstance(items, list) or not items or len(items) > 64:
                self.send_json(400, {"error": "That group key was not shared."})
                return
            devices = load_devices()
            my_ids = {row.get("device_id") for row in devices if row.get("user") == key}
            total = 0
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    epoch = int(item.get("epoch") or 0)
                except (TypeError, ValueError):
                    continue
                rows = g["epochs"].get(str(epoch))
                if not isinstance(rows, list) or not rows:
                    continue
                if not any(isinstance(w, dict) and w.get("user") == key and w.get("device_id") in my_ids for w in rows):
                    continue
                wraps = take_wraps(item.get("wraps"), GROUP_BATCH_MAX) or []
                total += len(wraps)
                if total > GROUP_BATCH_MAX:
                    break
                have = {(w.get("user"), w.get("device_id")) for w in rows if isinstance(w, dict)}
                for wrap in wraps:
                    owner = device_owner(devices, wrap["device_id"])
                    member = g["members"].get(owner)
                    if not isinstance(member, dict) or int(member.get("epoch_from") or 1) > epoch:
                        continue
                    if (owner, wrap["device_id"]) in have:
                        continue
                    wrap["user"] = owner
                    rows.append(wrap)
                    have.add((owner, wrap["device_id"]))
                    changed = True
            if changed:
                save_group(g)
        self.send_json(200, {"ok": True})

    # ---- 0.23.1 invite pack ----
    def _invite_limited(self, user):
        """Shared limit for app invites and group links: per account and per address. Sends 429 and returns True when hit."""
        ip = client_ip_of(self)
        wait = max(rate_wait("invite-user", user["username_key"], INVITE_USER_LIMITS),
                   rate_wait("invite-ip", ip, INVITE_IP_LIMITS))
        if wait:
            self.send_json(429, {"error": "You've made a lot of invites. Try again in %s." % wait_words(wait), "retry_after": wait})
            return True
        return False

    # Count one invite made, against both the person and their address (invite rate limits).
    def _invite_counted(self, user):
        rate_note("invite-user", user["username_key"])
        rate_note("invite-ip", client_ip_of(self))

    # Rate-limit invite link checks and joins per address and per account; replies 429 when over the limit.
    def _redeem_limited(self, user):
        ip = client_ip_of(self)
        wait = max(rate_wait("redeem-ip", ip, REDEEM_IP_LIMITS), rate_wait("redeem-user", user["username_key"], REDEEM_USER_LIMITS))
        if wait:
            self.send_json(429, {"error": "Too many invite link tries. Try again in %s." % wait_words(wait), "retry_after": wait})
            return True
        rate_note("redeem-ip", ip)
        rate_note("redeem-user", user["username_key"])
        return False

    def glink_create(self, user):
        """Owner/admin makes the group's invite link (any older link of this group stops working: 'Reset link')."""
        key = user["username_key"]
        if self._invite_limited(user):
            return
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            role = group_role(g, key)
            if role != "owner":   # 26.10.13: owner-only
                self.send_json(403, {"error": "Only the group owner can make an invite link."})
                return
            lookup = data.get("lookup")
            card = data.get("card") if isinstance(data.get("card"), dict) else {}
            seal = data.get("seal") if isinstance(data.get("seal"), dict) else {}
            expiry = data.get("expires") if data.get("expires") in GLINK_EXPIRY else "7d"
            try:
                max_uses = int(data.get("max_uses") or 0)
            except (TypeError, ValueError):
                max_uses = -1
            try:
                seal_epoch = int(seal.get("epoch") or 0)
            except (TypeError, ValueError):
                seal_epoch = 0
            ok = (isinstance(lookup, str) and HEX64.match(lookup)
                  and b64_ok(card.get("iv") or "", 8, 64) and b64_ok(card.get("ct") or "", 16, 600)
                  and b64_ok(seal.get("iv") or "", 8, 64) and b64_ok(seal.get("ct") or "", 16, 400)
                  and 1 <= seal_epoch <= int(g["epoch"]) and 0 <= max_uses <= GLINK_MAX_USES)
            if not ok:
                self.send_json(400, {"error": "That invite link was not made. Refresh and try again."})
                return
            if any(r.get("lookup") == lookup for r in _glinks):
                self.send_json(400, {"error": "That invite link was not made. Try again."})
                return
            now = int(time.time())
            for r in _glinks:
                if r.get("g") == g["id"] and not r.get("revoked"):
                    r["revoked"] = True
                    r["revoked_ts"] = now
            row = {
                "id": secrets.token_hex(8), "g": g["id"], "by": key, "created": now,
                "expires": (now + GLINK_EXPIRY[expiry]) if GLINK_EXPIRY[expiry] else 0,
                "max_uses": max_uses, "uses": 0, "revoked": False, "lookup": lookup,
                "card": {"iv": card["iv"], "ct": card["ct"]},
                "seal": {"epoch": seal_epoch, "iv": seal["iv"], "ct": seal["ct"]},
            }
            _glinks.append(row)
            save_glinks()
            self._invite_counted(user)
            view = glink_view(row)
        self.send_json(200, {"ok": True, "link": view})

    def glink_revoke(self, user):
        """Turn the group's invite link off. Anyone holding the old link gets 'expired or reset'."""
        key = user["username_key"]
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            role = group_role(g, key)
            if role != "owner":   # 26.10.13: owner-only
                self.send_json(403, {"error": "Only the group owner can turn the link off."})
                return
            now = int(time.time())
            changed = False
            for r in _glinks:
                if r.get("g") == g["id"] and not r.get("revoked"):
                    r["revoked"] = True
                    r["revoked_ts"] = now
                    changed = True
            if changed:
                save_glinks()
        self.send_json(200, {"ok": True})

    # Find the invite link a join request proves it holds, or reply with why it can't be used (used up, expired,
    # revoked).
    def _glink_lookup(self, user, data):
        r = glink_find(data.get("proof") if isinstance(data, dict) else None)
        if not r:
            self.send_json(404, {"error": "That invite link is not valid. Check you copied all of it."})
            return None, None
        why = glink_state(r)
        if why != "ok":
            words = {"used": "This invite link has been used as many times as it allows. Ask for a new one.",
                     "gone": "That group no longer exists."}.get(why, GLINK_GONE)
            self.send_json(410, {"error": words, "why": why})
            return None, None
        return r, _groups.get(r["g"])

    def glink_peek(self, user):
        """Before joining: the sealed card (the browser opens it with the key from the link) and the head count."""
        if self._redeem_limited(user):
            return
        data = self.read_json_body() or {}
        with _store_lock:
            r, g = self._glink_lookup(user, data)
            if not r:
                return
            members = [m for m in g["members"].values() if isinstance(m, dict)]
            out = {"ok": True, "g": g["id"], "card": dict(r.get("card") or {}), "members": len(members),
                   "full": len(members) >= GROUP_MAX, "member": user["username_key"] in g["members"],
                   "expires": int(r.get("expires") or 0), "by": name_of(r.get("by") or ""),
                   "history": g.get("history") or "join"}
        self.send_json(200, out)

    def glink_redeem(self, user):
        """Join a group with a link. Moves the group to a new empty epoch; the joiner's browser or any member fills it."""
        if self._redeem_limited(user):
            return
        data = self.read_json_body() or {}
        key = user["username_key"]
        with _store_lock:
            r, g = self._glink_lookup(user, data)
            if not r:
                return
            if key in g["members"]:
                self.send_json(200, {"ok": True, "already": True, "group": self._group_reply(g, key)})
                return
            if publicmode.group_blocked(key, g["id"]):
                # 26.10.2: this person used "Leave and block" on the group; a link must not pull them back in.
                self.send_json(403, {"error": "You left and blocked this group. To join again, unblock it in Profile settings \u2192 Safety.", "blocked": True})
                return
            if len(g["members"]) >= GROUP_MAX:
                self.send_json(409, {"error": "This group is full (%d people). Ask an admin to make room." % GROUP_MAX, "full": True})
                return
            if len(groups_of(key)) >= GROUP_PER_USER:
                self.send_json(400, {"error": "You are in %d groups, the most allowed. Leave one first." % GROUP_PER_USER})
                return
            now = int(time.time())
            new_epoch = int(g["epoch"]) + 1
            history_all = (g.get("history") or "join") == "all"
            g["members"][key] = {"name": user["username"], "role": "member", "epoch_from": 1 if history_all else new_epoch,
                                 "since": now, "added_by": r.get("by") or "", "via": "link"}
            g["epoch"] = new_epoch
            g["epochs"][str(new_epoch)] = []
            r["uses"] = int(r.get("uses") or 0) + 1
            save_group(g)
            save_glinks()
            group_event(g, "join", key, r.get("by") or "")
            view = self._group_reply(g, key)
        self.send_json(200, {"ok": True, "group": view})

    def group_meta_refresh(self, user):
        """After a link join the joiner fills the new epoch without the name (it can't read the old one). A member
        who holds both keys re-seals the same name under the current epoch. No notice row: nothing visible changed."""
        key = user["username_key"]
        with _store_lock:
            data, g = self._group_body(user)
            if not g:
                return
            epoch = int(g["epoch"])
            try:
                asked = int(data.get("epoch") or 0)
            except (TypeError, ValueError):
                asked = 0
            old = g.get("meta") or {}
            devices = load_devices()
            my_ids = {row.get("device_id") for row in devices if row.get("user") == key}
            rows = g["epochs"].get(str(epoch)) or []
            holds = any(isinstance(w, dict) and w.get("user") == key and w.get("device_id") in my_ids for w in rows)
            meta = self._group_meta(data)
            if asked != epoch or int(old.get("epoch") or 0) >= epoch or not holds or not meta:
                self.send_json(200, {"ok": True, "skipped": True})
                return
            g["meta"] = {"epoch": epoch, "iv": meta["iv"], "ct": meta["ct"], "rev": int(old.get("rev") or 0) + 1}
            save_group(g)
        self.send_json(200, {"ok": True})

    def _claim_blob(self, blob, owner):
        """Attach an upload this account finished and that no message has claimed yet."""
        if blob is None:
            return None, ""
        if not isinstance(blob, dict):
            return None, "That file was not attached."
        blob_id = (blob.get("id") or "").lower()
        meta = blob_meta(blob_id)
        if not meta or meta.get("user") != owner or meta.get("message"):
            return None, "That file was not attached."
        try:
            chunks = int(blob.get("chunks"))
            nbytes = int(blob.get("bytes"))
        except (TypeError, ValueError):
            return None, "That file was not attached."
        if chunks != int(meta.get("chunks") or -1) or nbytes != int(meta.get("bytes") or -1):
            return None, "That file was not attached."
        return {"id": blob_id, "chunks": chunks, "bytes": nbytes}, ""

    def add_wraps(self, user):
        """Add missing direct-message key wraps. Deleted messages are left alone."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That message was not updated."})
            return
        msg_id = (data.get("id") or "").lower()
        wraps = take_wraps(data.get("wraps"), 16)
        if not HEX32.match(msg_id) or not wraps:
            self.send_json(400, {"error": "That message was not updated."})
            return
        key = user["username_key"]
        with _store_lock:
            msg = _by_id.get(msg_id)
            lobby = load_lobby()
            if not msg or msg.get("deleted") or msg.get("box") != "dm" or not can_see(msg, key, lobby):
                self.send_json(404, {"error": "That message was not found."})
                return
            devices = load_devices()
            allowed = {msg.get("a"), msg.get("b")}
            have = {wrap.get("device_id") for wrap in msg.get("wraps") or []}
            changed = False
            for wrap in wraps:
                owner = device_owner(devices, wrap["device_id"])
                if owner not in allowed or wrap["device_id"] in have:
                    continue
                wrap["user"] = owner
                msg.setdefault("wraps", []).append(wrap)
                have.add(wrap["device_id"])
                changed = True
            if changed:
                self._rewrite_messages(msg)
        self.send_json(200, {"ok": True})

    def _rewrite_messages(self, *changed):
        """Queue a full log rewrite after an in-place edit such as a tombstone or a new wrap (26.10.8: written by
        log_flush() after _store_lock is released; only the changed rows are serialized again)."""
        _note_pending(log_queue_rewrite(*changed) if changed else log_queue_rewrite(None))
        _idx_bump()

    def blob_start(self, user):
        """Begin an encrypted upload. The plaintext size is a number. The bytes are ciphertext."""
        data = self.read_json_body()
        if not data:
            self.send_json(400, {"error": "That file was not accepted."})
            return
        try:
            size = int(data.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            self.send_json(400, {"error": "That file is empty."})
            return
        if size > PLAIN_MAX:
            self.send_json(413, {"error": "That file is too large."})
            return
        chunks = chunk_count(size)
        if chunks > 40:
            self.send_json(413, {"error": "That file is too large."})
            return
        extra = size + chunks * 28
        if not budget_ok(extra):
            self.send_json(400, {"error": "Chat files have reached 10 GB."})
            return
        if not disk_ok(extra):
            self.send_json(400, {"error": "The server does not have room for that file."})
            return
        upload_id = secrets.token_hex(16)
        os.makedirs(CHUNK_DIR, exist_ok=True)
        os.chmod(CHUNK_DIR, 0o700)
        with _store_lock:
            drop_user_sessions(user["username_key"])
            atomic_write(session_path(upload_id), json.dumps({
                "id": upload_id,
                "user": user["username_key"],
                "size": size,
                "chunks": chunks,
                "created": int(time.time()),
            }))
        self.send_json(200, {"ok": True, "id": upload_id, "chunks": chunks})

    def blob_part(self, user):
        """Store one ciphertext piece. The caller checks the index and the size."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > PART_MAX + 1024 * 1024:
            self.close_connection = True
            self.send_json(413, {"error": "That piece is too large."})
            return
        try:
            body = self._read_exact(length)
        except OSError:
            self.send_json(400, {"error": "That piece did not upload."})
            return
        if body is None:
            self.send_json(400, {"error": "That piece did not upload."})
            return
        parsed = parse_multipart(self.headers.get("Content-Type"), body)
        if not parsed:
            self.send_json(400, {"error": "That piece did not upload."})
            return
        fields, file_part = parsed
        upload_id = (fields.get("upload_id") or "").lower()
        try:
            index = int(fields.get("index") or "-1")
        except ValueError:
            index = -1
        if not HEX32.match(upload_id) or file_part is None or index < 0 or index > 40:
            self.send_json(400, {"error": "That piece did not upload."})
            return
        with _store_lock:
            meta = read_json(session_path(upload_id), {})
            if not isinstance(meta, dict) or meta.get("user") != user["username_key"]:
                self.send_json(400, {"error": "That piece did not upload."})
                return
            if index >= int(meta.get("chunks") or 0):
                self.send_json(400, {"error": "That piece did not upload."})
                return
            expected = expect_part(int(meta["size"]), index, int(meta["chunks"]))
            if len(file_part) != expected or len(file_part) > PART_MAX:
                self.send_json(400, {"error": "That piece did not upload."})
                return
            if not budget_ok(len(file_part)) or not disk_ok(len(file_part)):
                self.send_json(400, {"error": "The server does not have room for that file."})
                return
            os.makedirs(CHUNK_DIR, exist_ok=True)
            path = part_path(upload_id, index)
            tmp = path + ".incoming"
            try:
                with open(tmp, "wb") as fh:
                    fh.write(file_part)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
                os.chmod(path, 0o600)
            except OSError:
                self.send_json(400, {"error": "The server does not have room for that file."})
                return
        self.send_json(200, {"ok": True})

    def blob_finish(self, user):
        """Assemble pieces into a file. It is not visible in a chat until a message claims it."""
        data = self.read_json_body() or {}
        upload_id = (data.get("id") or "").lower()
        if not HEX32.match(upload_id):
            self.send_json(400, {"error": "That file was not finished."})
            return
        with _store_lock:
            meta = read_json(session_path(upload_id), {})
            if not isinstance(meta, dict) or meta.get("user") != user["username_key"]:
                existing = blob_meta(upload_id)
                if existing and existing.get("user") == user["username_key"]:
                    self.send_json(200, {"ok": True, "id": upload_id, "chunks": existing["chunks"], "bytes": existing["bytes"]})
                    return
                self.send_json(400, {"error": "That file was not finished."})
                return
            chunks = int(meta["chunks"])
            total = 0
            for index in range(chunks):
                path = part_path(upload_id, index)
                try:
                    total += os.path.getsize(path)
                except OSError:
                    self.send_json(400, {"error": "That piece did not upload."})
                    return
            dest = os.path.join(BLOB_DIR, upload_id)
            os.makedirs(dest, exist_ok=True)
            os.chmod(dest, 0o700)
            for index in range(chunks):
                os.replace(part_path(upload_id, index), os.path.join(dest, "%d.bin" % index))
                os.chmod(os.path.join(dest, "%d.bin" % index), 0o600)
            info = {
                "id": upload_id,
                "user": user["username_key"],
                "chunks": chunks,
                "bytes": total,
                "plain_size": int(meta["size"]),
                "created": int(time.time()),
                "message": "",
            }
            atomic_write(os.path.join(dest, "meta.json"), json.dumps(info))
            drop_session(upload_id)
        self.send_json(200, {"ok": True, "id": upload_id, "chunks": chunks, "bytes": total})

    def blob_get(self, user, path):
        """Send one ciphertext piece to someone who may see that message, or to the uploader before it is claimed."""
        parts = path.split("/")
        if len(parts) != 5:
            self.send_json(404, {"error": "That file was not found."})
            return
        blob_id = parts[3].lower()
        try:
            index = int(parts[4])
        except ValueError:
            index = -1
        meta = blob_meta(blob_id)
        if not meta or index < 0 or index >= int(meta.get("chunks") or 0):
            self.send_json(404, {"error": "That file was not found."})
            return
        key = user["username_key"]
        with _store_lock:
            lobby = load_lobby_ro()
            allowed = meta.get("user") == key and not meta.get("message")
            seq = _blob_seq.get(blob_id)
            if seq is not None:
                msg = message_by_seq(seq)
                if msg and can_see(msg, key, lobby):
                    allowed = True
        if not allowed:
            self.send_json(404, {"error": "That file was not found."})
            return
        file_path = os.path.join(BLOB_DIR, blob_id, "%d.bin" % index)
        try:
            with open(file_path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json(404, {"error": "That file was not found."})
            return
        # 26.10.8 perf: a blob piece is ciphertext and never changes under its random id, so the browser may keep it.
        # "private" keeps Cloudflare and other shared caches out; the access check above still runs on every miss.
        self.send_bytes(200, body, "application/octet-stream", [
            ("Cache-Control", "private, max-age=31536000, immutable"),
            ("X-Content-Type-Options", "nosniff"),
        ])

    def gifs(self, user, query):
        """GIF search for a signed-in account. The query leaves the browser."""
        text = (query.get("q") or [""])[0]
        raw = (query.get("page") or ["1"])[0]
        page = int(raw) if raw.isdigit() and len(raw) <= 3 else 0
        gifs, more, error = klipy_search(user["username_key"], text, page)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_json(200, {"ok": True, "gifs": gifs, "page": page, "next": more})

    def gif_file(self, user, query):
        """Proxy one KLIPY GIF so the page can encrypt it before sending."""
        slug = (query.get("slug") or [""])[0]
        blob, error = klipy_bytes(user["username_key"], slug)
        if error:
            self.send_json(400, {"error": error})
            return
        self.send_bytes(200, blob, "image/gif", [
            ("Cache-Control", "private, max-age=3600"),
            ("X-Content-Type-Options", "nosniff"),
        ])

    def unfurl_get(self, user, query):
        """Link preview metadata for a signed-in account (links-yt). Fails quietly."""
        url = (query.get("url") or [""])[0]
        if not unfurl.allowed(user["username_key"], 40):
            self.send_json(429, {"error": "Too many previews. Try again soon."})
            return
        self.send_json(200, {"ok": True, "preview": unfurl.unfurl(url)})

    def unfurl_image(self, user, query):
        """Proxy a preview picture so the site never sees the reader's address."""
        if not unfurl.allowed(user["username_key"] + ":img", 120):
            self.send_json(429, {"error": "Too many previews."})
            return
        got = unfurl.image((query.get("t") or [""])[0])
        if not got:
            self.send_json(404, {"error": "No preview."})
            return
        self.send_bytes(200, got[1], got[0], [
            ("Cache-Control", "private, max-age=86400"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'none'"),
        ])


def cookie_header(token):
    """Session cookie. HttpOnly and Secure so a page script cannot read it. About 14 days."""
    return "huzz_chat_session=%s; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=%d" % (token, accounts.SESSION_SECONDS)


def verify_cookie_header(token):
    """Remembers that this browser already passed the email code. Logout does not clear it."""
    return "huzz_chat_verified=%s; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=%d" % (token, accounts.VERIFY_SECONDS)


def clear_cookie_header():
    """Expire the session cookie. The verification cookie is left in place on purpose."""
    return "huzz_chat_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"


def _arm_factor(user_key):
    """Allow five authenticator tries for the next five minutes of this sign-in."""
    now = int(time.time())
    with _store_lock:
        store = load_key_store()
        store["factors"][user_key] = {"exp": now + 300, "tries": 0}
        save_key_store(store)


def _take_factor(user, typed):
    """Check an authenticator or backup code for a sign-in that already passed the password."""
    key = user["username_key"]
    now = int(time.time())
    with _store_lock:
        store = load_key_store()
        row = store["factors"].get(key) or {}
        if not row or now > int(row.get("exp") or 0) or int(row.get("tries") or 0) >= 5:
            store["factors"].pop(key, None)
            save_key_store(store)
            return False
    if not accounts.consume_factor(user["username"], typed, allow_backup=True):
        with _store_lock:
            store = load_key_store()
            row = store["factors"].get(key) or {}
            tries = int(row.get("tries") or 0) + 1
            if tries >= 5 or not row:
                store["factors"].pop(key, None)
            else:
                row["tries"] = tries
                store["factors"][key] = row
            save_key_store(store)
        return False
    with _store_lock:
        store = load_key_store()
        store["factors"].pop(key, None)
        save_key_store(store)
    return True


def _reset_budget():
    """Cap reset emails so a script cannot flood the mailbox. The page still says the same thing."""
    now = time.time()
    while _reset_sent and now - _reset_sent[0] > 600:
        _reset_sent.pop(0)
    if len(_reset_sent) >= 8:
        return False
    _reset_sent.append(now)
    return True


RESET_NOTE = "If that account can receive email, a reset code is on its way. It expires in 10 minutes."


def _issue_reset(username):
    """Email a reset code when the account exists. Missing accounts do nothing. The address is not printed."""
    if not isinstance(username, str) or not accounts.USERNAME_RE.match(username.strip()):
        return
    user = accounts.find_user(username)
    if not user or not user.get("password"):
        return
    email = (user.get("email") or "").strip()
    if not email:
        return
    now = int(time.time())
    key = user["username_key"]
    with _store_lock:
        store = load_key_store()
        current = store["resets"].get(key) or {}
        if now - int(current.get("sent") or 0) < 60:
            return
        if not _reset_budget():
            return
        code = "%06d" % secrets.randbelow(1000000)
        store["resets"][key] = {
            "hash": accounts.code_hash(key, "reset:" + code),
            "exp": now + 600,
            "tries": 0,
            "sent": now,
        }
        save_key_store(store)
    # 26.10.2 fix: reset emails come From the server's own sender (BC_MAIL_FROM), never the user's own
    # name and address (mail services such as Postmark refuse or flag a From they don't own).
    if accounts.send_reset_code(email, code):
        return
    with _store_lock:
        store = load_key_store()
        prev = store["resets"].get(key) or {}
        if prev.get("hash") == accounts.code_hash(key, "reset:" + code):
            store["resets"][key] = {"sent": int(prev.get("sent") or now)}
            save_key_store(store)


RESET_LINK_SECONDS = 3600
RESET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{40,64}")
_admin_reset_sent = []


def _audit(text):
    """One line in the server log for owner/admin actions (no addresses, no tokens)."""
    print("admin audit: %s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), text), flush=True)


def _issue_admin_reset(target, admin):
    """Owner/admin reset: same 'resets' store as the self-service code (hashed, single-use, 5 tries), but a long
    random token sent as a link that works for an hour. A newer code or link replaces the older one."""
    email = (target.get("email") or "").strip()
    who = admin.get("username") or "?"
    name = target.get("username") or ""
    if not email or not target.get("password"):
        _audit("reset-email by=%s user=%s result=no_email" % (who, name))
        return "no_email"
    now = int(time.time())
    key = target["username_key"]
    with _store_lock:
        store = load_key_store()
        current = store["resets"].get(key) or {}
        if now - int(current.get("sent") or 0) < 60:
            _audit("reset-email by=%s user=%s result=wait" % (who, name))
            return "wait"
        while _admin_reset_sent and now - _admin_reset_sent[0] > 3600:
            _admin_reset_sent.pop(0)
        if len(_admin_reset_sent) >= 20:
            _audit("reset-email by=%s user=%s result=busy" % (who, name))
            return "busy"
        _admin_reset_sent.append(now)
        token = secrets.token_urlsafe(32)
        store["resets"][key] = {"hash": accounts.code_hash(key, "reset:" + token), "exp": now + RESET_LINK_SECONDS,
                                "tries": 0, "sent": now, "kind": "link"}
        save_key_store(store)
    link = "%s/#reset=%s&token=%s" % (bcconfig.PUBLIC_URL, quote(name, safe=""), token)
    ok = accounts.send_reset_link(email, name, link, RESET_LINK_SECONDS // 60)
    if not ok:
        with _store_lock:
            store = load_key_store()
            prev = store["resets"].get(key) or {}
            if prev.get("hash") == accounts.code_hash(key, "reset:" + token):
                store["resets"].pop(key, None)
                save_key_store(store)
    _audit("reset-email by=%s user=%s result=%s" % (who, name, "sent" if ok else "mail_failed"))
    return "ok" if ok else "fail"


def _keep_reset_sent(store, key, current):
    """Drop a used or exhausted reset code but remember when mail last went out."""
    sent = int(current.get("sent") or 0)
    if sent:
        store["resets"][key] = {"sent": sent}
    else:
        store["resets"].pop(key, None)


def _finish_reset(user, code, factor):
    """Check the email code and, when two-factor is on, the authenticator or backup code.

    Returns ok, totp (code matched but the second factor was left blank), or bad.
    A blank second factor does not burn the email code.
    """
    raw = (code or "").strip()
    typed = raw if RESET_TOKEN_RE.fullmatch(raw) else "".join(ch for ch in raw if ch.isdigit())
    key = user["username_key"]
    now = int(time.time())
    with _store_lock:
        store = load_key_store()
        current = store["resets"].get(key) or {}
        expired = (not current.get("hash")) or now > int(current.get("exp") or 0) or int(current.get("tries") or 0) >= 5
        if expired:
            _keep_reset_sent(store, key, current)
            save_key_store(store)
            return "bad"
        try:
            matched = hmac.compare_digest(current.get("hash") or "", accounts.code_hash(key, "reset:" + typed))
        except ValueError:
            matched = False
        if not (re.fullmatch(r"\d{6}", typed) or RESET_TOKEN_RE.fullmatch(typed)) or not matched:
            current["tries"] = int(current.get("tries") or 0) + 1
            if current["tries"] >= 5:
                _keep_reset_sent(store, key, current)
            else:
                store["resets"][key] = current
            save_key_store(store)
            return "bad"
        needs = accounts.totp_enabled(user)
        if needs and not (factor or "").strip():
            return "totp"
        if not needs:
            _keep_reset_sent(store, key, current)
            save_key_store(store)
            return "ok"
    if not accounts.consume_factor(user["username"], factor, allow_backup=True):
        with _store_lock:
            store = load_key_store()
            current = store["resets"].get(key) or {}
            if current.get("hash"):
                current["tries"] = int(current.get("tries") or 0) + 1
                if current["tries"] >= 5:
                    _keep_reset_sent(store, key, current)
                else:
                    store["resets"][key] = current
                save_key_store(store)
        return "bad"
    with _store_lock:
        store = load_key_store()
        current = store["resets"].get(key) or {}
        try:
            still = hmac.compare_digest(current.get("hash") or "", accounts.code_hash(key, "reset:" + typed))
        except ValueError:
            still = False
        if not still:
            return "bad"
        _keep_reset_sent(store, key, current)
        save_key_store(store)
    return "ok"


def email_code_for(user):
    """Email a 6-digit code and store only its hash. Rate-limits another send for about a minute."""
    email = (user.get("email") or "").strip()
    # 26.10.2 fix: sign-in codes come From the server's own sender (BC_MAIL_FROM), never the user's own
    # name and address; an empty sender makes accounts.send_code use BC_MAIL_FROM. No Reply-To is added.
    sender = ""
    if not email:
        return "fail"
    now = int(time.time())
    code = "%06d" % secrets.randbelow(1000000)
    key = user["username_key"]
    with _store_lock:
        store = load_key_store()
        current = store["codes"].get(key) or {}
        if now - int(current.get("sent") or 0) < 60:
            if current.get("hash") and int(current.get("exp") or 0) > now:
                return "wait"
            return "soon"
        store["codes"][key] = {
            "hash": accounts.code_hash(key, code),
            "exp": now + 600,
            "tries": 0,
            "sent": now,
        }
        save_key_store(store)
    if not accounts.send_code(email, code, sender):
        with _store_lock:
            store = load_key_store()
            prev = store["codes"].get(key) or {}
            if prev.get("hash") == accounts.code_hash(key, code):
                store["codes"][key] = {"sent": int(prev.get("sent") or now)}
                save_key_store(store)
        return "fail"
    return "ok"


def take_email_code(user_key, typed):
    """Check a code and forget it. Returns the stored backup payload, or None when the code is wrong."""
    typed = "".join(ch for ch in (typed or "") if ch.isdigit())
    with _store_lock:
        store = load_key_store()
        current = store["codes"].get(user_key) or {}
        backup = store["backups"].get(user_key) or {}
        now = int(time.time())
        sent = int(current.get("sent") or 0)

        # Keep only the time of the last code sent (so the resend cooldown still works) or drop the row.
        def keep_sent():
            if sent:
                store["codes"][user_key] = {"sent": sent}
            else:
                store["codes"].pop(user_key, None)

        if not current.get("hash") or now > int(current.get("exp") or 0) or int(current.get("tries") or 0) >= 5:
            keep_sent()
            save_key_store(store)
            return None
        try:
            matched = hmac.compare_digest(current.get("hash") or "", accounts.code_hash(user_key, typed))
        except ValueError:
            matched = False
        if not re.fullmatch(r"\d{6}", typed) or not matched:
            current["tries"] = int(current.get("tries") or 0) + 1
            if current["tries"] >= 5:
                keep_sent()
            else:
                store["codes"][user_key] = current
            save_key_store(store)
            return None
        keep_sent()
        save_key_store(store)
        if backup.get("ct"):
            return {"salt": backup.get("salt"), "iv": backup.get("iv"), "ct": backup.get("ct")}
        return {}


# ---------------------------------------------------------------------------
# Self-hosting helpers: schema version, optional branding.
# With no BC_INSTANCE_NAME / BC_ACCENT / BC_BACKGROUND and the default
# BC_PUBLIC_URL, none of the rewriting below runs and pages are served as-is.
# ---------------------------------------------------------------------------

# Migrations run at startup, one step at a time: MIGRATIONS[n] upgrades the
# state directory from schema n to n + 1. There are none yet (schema 1 is the
# first released layout), so the hook only records the version.
MIGRATIONS = {}


# The data format version written in the state folder (0 when missing or unreadable).
def _read_schema_version():
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


# Record the data format version, atomically and private to the server user.
def _write_schema_version(version):
    tmp = SCHEMA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % version)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SCHEMA_PATH)


def _snapshot_state(tag):
    """Copy the top-level state files (not blobs) before a migration so it can be undone by hand."""
    dest = os.path.join(STATE_DIR, "pre-migrate-%s-%d" % (tag, int(time.time())))
    os.makedirs(dest, exist_ok=True)
    os.chmod(dest, 0o700)
    for name in os.listdir(STATE_DIR):
        src = os.path.join(STATE_DIR, name)
        if os.path.isfile(src) and not name.endswith(".tmp"):
            shutil.copy2(src, os.path.join(dest, name))
    return dest


def migrate_state():
    """Bring the state directory up to SCHEMA_VERSION. Refuses to start on data from a newer version."""
    current = _read_schema_version()
    if current == 0:
        # No version file yet: this is the original layout (schema 1) or an empty directory.
        current = 1
    if current > SCHEMA_VERSION:
        raise SystemExit(
            "State in %s is schema %d but this server only knows schema %d. "
            "Install the newer server version (or restore a matching backup)." % (STATE_DIR, current, SCHEMA_VERSION))
    if current < SCHEMA_VERSION:
        snap = _snapshot_state("v%d" % current)
        print("huzz-chat: state snapshot before migration: %s" % snap, flush=True)
        while current < SCHEMA_VERSION:
            MIGRATIONS[current]()
            current += 1
    if _read_schema_version() != current:
        _write_schema_version(current)


_BRANDING = bcconfig.branding_enabled()
_ORG_ROW = directory.org_configured()   # 0.23.3-beta.12: org name + picture on the sign-in screen
_REWRITE_PAGES = _BRANDING or _ORG_ROW or not bcconfig.is_default_public_url()
PUBLIC_DESCRIPTION = "Private, end-to-end encrypted chat for family and friends. Find your group or sign in."
_ACCENT_RE = re.compile(r"^#[0-9a-f]{6}$")
_BG_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


# Escape text for safe use inside HTML text and attributes.
def _html_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _background_file():
    """(path, content type) for BC_BACKGROUND. A bare file name is looked up in STATE_DIR/branding/."""
    raw = bcconfig.BACKGROUND
    if not raw:
        return None
    path = raw if os.path.isabs(raw) else os.path.join(STATE_DIR, "branding", raw)
    kind = _BG_TYPES.get(os.path.splitext(path)[1].lower())
    if not kind or not os.path.isfile(path):
        return None
    return path, kind


def _on_accent(hex_color):
    """Black or white text, whichever reads better on the accent colour."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#071018" if lum > 0.5 else "#ffffff"


def instance_css():
    """Small stylesheet for the admin's accent colour and background. Users' own theme picks still win."""
    out = ["/* BranchChat instance branding (generated from BC_ACCENT / BC_BACKGROUND). */"]
    accent = bcconfig.ACCENT
    if _ACCENT_RE.match(accent):
        out.append(':root:not([data-palette]), html[data-palette="huzz"], html[data-palette="huzz"][data-scheme] { --accent: %s; --on-accent: %s; }' % (accent, _on_accent(accent)))
    if _background_file():
        out.append('body::before, html[data-scheme="light"] body::before { background-image: url("/instance/background") !important; }')
    return "\n".join(out) + "\n"


def org_row_html():
    """The organisation's picture and name, shown under the Branch Chat title on the sign-in screen (beta.12)."""
    raw, _kind = directory.configured_org_icon()
    img = ('<img class="org-pic" src="/directory-icon?h=%s" alt="" width="40" height="40">' % directory.icon_hash(raw)) if raw else ""
    label = _html_escape(directory.org_name()) if (bcconfig.get("BC_ORG_NAME", "").strip() or bcconfig.INSTANCE_NAME) else ""
    if not img and not label:
        return ""
    return '<p class="org-row">%s<span class="org-name">%s</span></p>' % (img, label)


_HEADER_LOGO_RE = re.compile(r'<span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span> '
                             r'(<a class="ver"[^>]*>[^<]*</a>)')


def org_header_html(ver_link):
    """In-app top banner (beta.12/13): the organisation's name in place of the Branch Chat wordmark, with a small "Branch Chat"
    credit (always shown on branded servers) and the version link on the line under the name. The org's picture replaces the
    BranchChat icon in the corner (see org_corner_icon_html), so it is not repeated here."""
    name = _html_escape(directory.org_name())
    return ('<span class="org-title" title="%s"><span class="org-text"><span class="org-name">%s</span>'
            '<span class="org-sub"><span class="org-credit">BranchChat&trade;</span> %s</span></span></span>' % (name, name, ver_link))


# 0.23.3-beta.13: with BC_ORG_ICON set, the org's picture REPLACES the Branch Chat icon in the top-left corner of the app.
_HEADER_ICON_RE = re.compile(r'<span class="bc-icon"[^>]*><img src="/favicon\.png"[^>]*></span>')
_FAVICON_LINKS_RE = re.compile(r'<link rel="icon"[^>]*>\n?')


# White-label servers: the org picture that replaces the Branch Chat icon in the top corner ("" when none is set).
def org_corner_icon_html():
    raw, _kind = directory.configured_org_icon()
    if not raw:
        return ""
    name = _html_escape(directory.org_name())
    return ('<span class="bc-icon org-icon" title="%s"><img class="org-pic" src="/directory-icon?h=%s" alt="%s" width="36" height="36"></span>'
            % (name, directory.icon_hash(raw), name))


def org_favicon_html():
    """Tab icon: one <link rel=icon> pointing at the org picture (beta.13). Home-screen/PWA icons stay Branch Chat's PNGs."""
    raw, kind = directory.configured_org_icon()
    if not raw:
        return ""
    return '<link rel="icon" type="%s" href="/directory-icon?h=%s" data-org="1">\n' % (directory.ICON_TYPES[kind], directory.icon_hash(raw))


# Personal credits (BC_CREDITS_FILE): read once at start-up. The About page has two empty markers,
# <!--bc-about-family--> (a section about the people behind this server) and <!--bc-icon-credit--> (who made the
# icon); with a credits file they are filled in, and the icon's hover tip on the chat page says who made it.
# Without one (the default, and on the public branchchat.app) the markers stay empty and no names are shown.
def _load_credits():
    if not bcconfig.CREDITS_FILE:
        return {}
    try:
        with open(bcconfig.CREDITS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        print("credits: could not read BC_CREDITS_FILE; showing no personal credits", flush=True)
        return {}


_CREDITS = _load_credits()


def apply_credits(path, body):
    """Fill the About page markers and the icon tip from BC_CREDITS_FILE, and the optional Contact link from
    BC_CONTACT_EMAIL. Does nothing when neither is set."""
    if not path.endswith(".html") or not (_CREDITS or bcconfig.CONTACT_EMAIL):
        return body
    text = body.decode("utf-8", "replace")
    # Contact link slot on the About page: filled only when BC_CONTACT_EMAIL is set; otherwise no link is shown.
    if bcconfig.CONTACT_EMAIL and "<!--bc-contact-->" in text:
        addr = _html_escape(bcconfig.CONTACT_EMAIL)
        text = text.replace("<!--bc-contact-->", ' &middot; <a href="mailto:' + addr + '">Contact</a>', 1)
    if not _CREDITS:
        return text.encode("utf-8")
    about = _CREDITS.get("about_html") or ""
    credit = (_CREDITS.get("icon_credit") or "").strip()
    if about and "<!--bc-about-family-->" in text:
        text = text.replace("<!--bc-about-family-->", "<section>\n" + about + "\n</section>", 1)
    if credit:
        safe = _html_escape(credit)
        text = text.replace("<!--bc-icon-credit-->", '<p class="caption">' + safe + "</p>", 1)
        text = text.replace('data-tip="BranchChat" tabindex="0"><img src="/favicon.png" alt="BranchChat icon"',
                            'data-tip="' + safe + '" tabindex="0"><img src="/favicon.png" alt="BranchChat icon, ' + safe + '"', 1)
    return text.encode("utf-8")


def rewrite_page(path, body):
    """Apply the instance name, public address and branding stylesheet to a page or the manifest."""
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return body
    if not bcconfig.is_default_public_url():
        text = text.replace(bcconfig.DEFAULT_PUBLIC_URL, PUBLIC_URL)
    # 26.10.3: the public front door (branchchat.app) describes itself as the public home, not as a private family
    # server; private servers keep the original wording.
    if frontdoor.ENABLED:
        text = text.replace("A private, invite-only chat for family and friends.", PUBLIC_DESCRIPTION)
    name = bcconfig.INSTANCE_NAME
    if path.endswith(".webmanifest"):
        if name:
            try:
                data = json.loads(text)
                data["name"] = name + " \u00b7 BranchChat"
                data["short_name"] = name[:24]
                text = json.dumps(data, indent=2)
            except ValueError:
                pass
        return text.encode("utf-8")
    if not GROUPS_ENABLED and path.endswith("index.html"):
        text = text.replace("<html", '<html data-groups="off"', 1)
    if _ORG_ROW and path.endswith("login.html") and "<!--bc-org-->" in text:
        text = text.replace("<!--bc-org-->", org_row_html(), 1)
    if _ORG_ROW and path.endswith("index.html") and "<header>" in text and _HEADER_LOGO_RE.search(text):
        text = _HEADER_LOGO_RE.sub(lambda m: org_header_html(m.group(1)), text, count=1).replace("<header>", '<header class="has-org">', 1)
        corner = org_corner_icon_html()
        if corner:
            text = _HEADER_ICON_RE.sub(lambda m: corner, text, count=1)
    if _ORG_ROW and (path.endswith("index.html") or path.endswith("login.html") or path.endswith("welcome.html")):
        fav = org_favicon_html()
        if fav and _FAVICON_LINKS_RE.search(text):
            text = _FAVICON_LINKS_RE.sub("", text)
            text = text.replace('<link rel="apple-touch-icon"', fav + '<link rel="apple-touch-icon"', 1) if '<link rel="apple-touch-icon"' in text else text.replace("</head>", fav + "</head>", 1)
    if name:
        text = re.sub(r"<title>([^<]*)</title>",
                      lambda m: "<title>%s \u00b7 %s</title>" % (_html_escape(name), m.group(1)), text, count=1)
    if _BRANDING and "</head>" in text:
        text = text.replace("</head>", '<link rel="stylesheet" href="/instance.css">\n</head>', 1)
    return text.encode("utf-8")


def main():
    """Listen on BC_HOST:BC_PORT (default 127.0.0.1:8766). The tunnel is the public path. Encode is a different port and service."""
    os.makedirs(STATE_DIR, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    os.makedirs(BLOB_DIR, exist_ok=True)
    os.chmod(BLOB_DIR, 0o700)
    os.makedirs(CHUNK_DIR, exist_ok=True)
    os.chmod(CHUNK_DIR, 0o700)
    migrate_state()
    load_messages()
    threading.Thread(target=_log_flusher, name="log-flusher", daemon=True).start()   # 26.10.8
    threading.Thread(target=_presence_ticker, name="presence-ticker", daemon=True).start()
    load_groups()
    load_glinks()
    sweep_old()
    try:
        welcomebot.init(sys.modules[__name__])  # 26.10.3: Helper bot (does nothing while BC_WELCOME_BOT is off)
    except Exception as exc:
        print("[welcome-bot] not started: %s" % exc.__class__.__name__, flush=True)
    featuremail.init()   # 26.10.4: new-account "What's new" email worker (does nothing while switched off)
    snap_prefs_since()   # 26.10.2: note when per-account delete animations arrived here (only written the first time)
    # 0.23.3-beta.15: the Administrator page reuses the sign-in helpers above for its re-verification step.
    adminpage.init(is_owner=is_owner, email_code_for=email_code_for, take_email_code=take_email_code,
                   arm_factor=_arm_factor, take_factor=_take_factor, version=BC_VERSION_LABEL, tools=_admin_tools())
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("huzz-chat listening on %s:%d" % (HOST, PORT), flush=True)
    adminpage.started_ok()
    directory.start_reporter(PUBLIC_URL)
    directory.start_admin_listener(BC_VERSION_LABEL)
    whatsnew_since(bcconfig.VERSION)   # 26.10.10: stamp when this version went live (What's new pop-up)
    server.serve_forever()



def _admin_tools():
    """Extra Administrator page tools (merged from 0.22.8-public.13): the report queue and language-filter mutes.
    They only appear when those features are switched on (BC_REPORT_BLOCK, Lobby link filter / language lock), so a
    family server's page is unchanged. Group reports (26.10.2) come through report_rows() like any other report.
    Each tool is (method, sub-path) -> function(handler, user); adminpage.route only calls them for a verified owner."""
    tools = {}
    if publicmode.REPORT_BLOCK:
        # 26.10.2: pass the server module so group reports show whether the group is still open and its size.
        tools[("GET", "reports")] = lambda h, user: h.send_json(200, {"ok": True, "reports": publicmode.report_rows(publicmode._srv(h))})
        tools[("POST", "reports/status")] = publicmode.report_status
        # 26.10.2: close a reported group from the Administrator page (same action as Close group in the app's
        # Reports list). adminpage.route only calls tools for the verified owner, matching group_close's owner-only rule.
        tools[("POST", "groups/close")] = publicmode.group_close
    if getattr(publicmode, "LOBBY_CHECK", False):
        tools[("GET", "filter")] = lambda h, user: h.send_json(200, dict(publicmode.filter_overview(), ok=True))

        def unmute(h, user):
            data = h.read_json_body() or {}
            other = accounts.find_user(data.get("username") or "") if isinstance(data.get("username"), str) else None
            if not other:
                h.send_json(400, {"error": "That account was not found."})
                return
            publicmode.unmute(other["username_key"], user["username"])
            h.send_json(200, {"ok": True})
        tools[("POST", "filter/unmute")] = unmute
    return tools


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 0.23.3-beta.15: if this start came right after "Apply" on the Administrator page and it failed,
        # put the previous settings file back and start again. Otherwise this returns and the error is raised.
        adminpage.recover_after_failed_start()
        raise
