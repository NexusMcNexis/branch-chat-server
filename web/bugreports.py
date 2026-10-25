"""26.10.5: bug reports from the in-app "Report a bug" form.

Every report is one JSON line appended to a file per day (server local date):
    <STATE_DIR>/bug-reports/YYYY-MM-DD.jsonl
Screenshots (optional, up to MAX_SHOTS) are saved next to it:
    <STATE_DIR>/bug-reports/shots/<report id>-<n>.<png|jpg|heic|webp|gif>
A nightly review job reads yesterday's file, then checks each report against the real code and UI before
anything is acted on. Reports are never shown to other users and nothing here runs code from a report.
"""
import base64
import json
import os
import re
import secrets
import threading
import time

import bcconfig

DIR = os.path.join(bcconfig.STATE_DIR, "bug-reports")
SHOT_DIR = os.path.join(DIR, "shots")
MAX_BODY = 24 * 1024 * 1024      # whole request, including base64 screenshots
MAX_SHOTS = 4
MAX_SHOT_BYTES = 5 * 1024 * 1024  # each screenshot after decoding
MAX_TEXT = 4000
PER_DAY = 20                      # per account; stops one person flooding the nightly review
_LOCK = threading.Lock()
_KINDS = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp", b"GIF8": "gif"}
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as f:
        VERSION = f.read().strip()
except OSError:
    VERSION = ""


def _clean(value, limit):
    """Plain text only: drop control characters (keep newlines and tabs) and cap the length."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()[:limit]


# HEIC/HEIF (iPhone and iPad photos): an ISO box file whose first box is "ftyp" with a HEIF brand.
# The servers have no HEIC decoder, so these are stored as-is (.heic); open them on a Mac/iPhone or convert later.
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"heif"}


def _kind(raw):
    if raw[4:8] == b"ftyp" and raw[8:12] in _HEIF_BRANDS:
        return "heic"
    for magic, ext in _KINDS.items():
        if raw.startswith(magic) and (ext != "webp" or raw[8:12] == b"WEBP"):
            return ext
    return None


def _count_today(path, user_key):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if ('"user_key": %s' % json.dumps(user_key)) in line)
    except OSError:
        return 0


def submit(user, data, headers):
    """Store one report. Returns (http status, reply dict)."""
    desc = _clean(data.get("description"), MAX_TEXT)
    if len(desc) < 5:
        return 400, {"error": "Please describe what you saw."}
    shots_in = data.get("screenshots") or []
    if not isinstance(shots_in, list) or len(shots_in) > MAX_SHOTS:
        return 400, {"error": "You can attach up to %d screenshots." % MAX_SHOTS}
    decoded = []
    for item in shots_in:
        b64 = item.get("data", "") if isinstance(item, dict) else ""
        b64 = b64.split(",", 1)[-1] if isinstance(b64, str) else ""
        try:
            raw = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return 400, {"error": "A screenshot could not be read."}
        ext = _kind(raw)
        if not ext or len(raw) > MAX_SHOT_BYTES:
            return 400, {"error": "Screenshots must be pictures (PNG, JPEG, HEIC, WebP or GIF) under 5 MB each."}
        decoded.append((raw, ext, _clean(item.get("name"), 120)))
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    path = os.path.join(DIR, day + ".jsonl")
    rid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(3)
    with _LOCK:
        os.makedirs(SHOT_DIR, exist_ok=True)
        if _count_today(path, user["username_key"]) >= PER_DAY:
            return 429, {"error": "You have sent a lot of reports today. Thank you! Please try again tomorrow."}
        shots = []
        for n, (raw, ext, name) in enumerate(decoded, 1):
            fname = "%s-%d.%s" % (rid, n, ext)
            with open(os.path.join(SHOT_DIR, fname), "wb") as f:
                f.write(raw)
            shots.append({"file": "shots/" + fname, "bytes": len(raw), "name": name})
        row = {
            "id": rid,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
            "server": {"name": bcconfig.INSTANCE_NAME, "url": bcconfig.PUBLIC_URL, "version": VERSION},
            "user": user.get("username", ""),
            "user_key": user["username_key"],
            "description": desc,
            "steps": _clean(data.get("steps"), MAX_TEXT),
            "where": _clean(data.get("where"), 300),
            "env": {
                "layout": _clean(env.get("layout"), 20),          # "desktop" or "mobile" (same breakpoint as the left panel)
                "viewport": _clean(env.get("viewport"), 40),
                "platform": _clean(env.get("platform"), 80),
                "standalone": bool(env.get("standalone")),      # installed app / home-screen mode
                "theme": _clean(env.get("theme"), 20),
                "client_version": _clean(env.get("version"), 40),
                "ua": _clean(headers.get("User-Agent"), 400),   # taken from the request, not trusted from the form
            },
            "screenshots": shots,
            "status": "new",   # the nightly reviewer records its verdict elsewhere; this file is append-only
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 200, {"ok": True, "id": rid}
