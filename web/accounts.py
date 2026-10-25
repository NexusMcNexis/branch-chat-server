"""Accounts for Branch Chat Server.

Passwords are scrypt hashes. Invite links store only a hash of the token.

A session row is the real logout time. The cookie signature only has
to still be in the future. Logout deletes that row. A password change
bumps session_version, which drops older cookies and the verification
cookie. Logout does not clear the verification cookie.

Optional authenticator codes live on the account row. The server has
to see that secret to check it. Backup codes are stored as hashes.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from email.utils import formataddr, formatdate, make_msgid, parseaddr

import bcconfig
import mailtemplates

STATE_DIR = bcconfig.STATE_DIR
ACCOUNTS_PATH = os.path.join(STATE_DIR, "accounts.json")
SESSION_PATH = os.path.join(STATE_DIR, "session.key")
SESSIONS_PATH = os.path.join(STATE_DIR, "sessions.json")
INVITE_SECONDS = 72 * 3600
# How long an emailed sign-in or reset code works. server.py stores codes with exp = now + 600 (10 minutes);
# the emails quote this number, so keep the two in step.
CODE_MINUTES = 10
SESSION_SECONDS = 400 * 24 * 3600  # stay signed in; slides forward on every visit
VERIFY_SECONDS = 400 * 24 * 3600  # trusted device remembered
SCRYPT_N = 2 ** 14
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
NAME_CHECK = None  # public server only: publicmode sets a name filter (links / mature language); None elsewhere
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAIL_FROM = bcconfig.MAIL_FROM
# The brand name used in outgoing emails (subjects, the text inside them and the "via" sender name).
# It is spelled "BranchChat", one word with a capital B and C, because that is the chosen spelling for email
# so it matches the "BranchChat <noreply@branchchat.app>" sender name. The website itself keeps "Branch Chat".
EMAIL_BRAND = "BranchChat"
PUBLIC_URL = bcconfig.PUBLIC_URL
INSTANCE_NAME = bcconfig.INSTANCE_NAME

_lock = threading.Lock()


def _read():
    """Load accounts.json. A missing file is an empty user list."""
    try:
        with open(ACCOUNTS_PATH) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("users", [])
    data.setdefault("invites", [])
    return data


def _write(data):
    """Write accounts.json mode 600."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ACCOUNTS_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, ACCOUNTS_PATH)
    os.chmod(ACCOUNTS_PATH, 0o600)


def _load_sessions():
    """Active browser logins. A deleted row is a logout."""
    try:
        with open(SESSIONS_PATH) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    items = data.get("sessions") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def _store_sessions(items):
    """Write the session list."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SESSIONS_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"sessions": items}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSIONS_PATH)
    os.chmod(SESSIONS_PATH, 0o600)


def _forget_user_sessions(key):
    """Drop every login for one account. Password change and account delete use this."""
    _store_sessions([item for item in _load_sessions() if item.get("user") != key])


def touch_session(user, claims):
    """Record a login the first time it is seen. It does not move the expiry."""
    exp = int(claims.get("exp") or 0)
    if exp < time.time():
        return
    key = user.get("username_key") or ""
    if not key:
        return
    sid = str(claims.get("sid") or "")
    ver = int(user.get("session_version") or claims.get("v") or 1)
    with _lock:
        items = _load_sessions()
        for item in items:
            if sid and item.get("sid") == sid:
                return
            if (
                not sid
                and item.get("user") == key
                and not item.get("sid")
                and int(item.get("v") or 0) == ver
                and int(item.get("exp") or 0) == exp
            ):
                return
        items.append({"user": key, "v": ver, "exp": exp, "sid": sid})
        _store_sessions(items)


def _sign_claims(claims):
    """HMAC the session claims with the server key. The browser cannot forge a cookie."""
    payload = json.dumps(claims, separators=(",", ":")).encode()
    sig = hmac.new(_session_key(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def session_row_exp(key, claims):
    """Authoritative expiry for this browser. The cookie exp is only a signature field."""
    sid = str(claims.get("sid") or "")
    ver = int(claims.get("v") or 0)
    now = time.time()
    if not key:
        return 0
    with _lock:
        for item in _load_sessions():
            if item.get("user") != key or int(item.get("v") or 0) != ver:
                continue
            item_sid = str(item.get("sid") or "")
            if sid:
                if item_sid != sid:
                    continue
            elif item_sid:
                continue
            exp = int(item.get("exp") or 0)
            if exp >= now:
                return exp
        return 0


def session_known(key, claims):
    """True when this browser's login is still on the active list."""
    if int(claims.get("exp") or 0) < time.time():
        return False
    return session_row_exp(key, claims) >= time.time()


def extend_session(user, seconds):
    """Keep this browser signed in for two weeks. There is no idle logout."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None, 0
    if seconds <= 0 or seconds > SESSION_SECONDS:
        return None, 0
    claims = user.get("_session") if isinstance(user, dict) else None
    if not isinstance(claims, dict):
        return None, 0
    sid = str(claims.get("sid") or "")
    ver = int(claims.get("v") or user.get("session_version") or 1)
    key = user.get("username_key") or ""
    if not key:
        return None, 0
    new_exp = int(time.time()) + SESSION_SECONDS
    new_sid = sid or secrets.token_hex(16)
    with _lock:
        items = _load_sessions()
        found = False
        for item in items:
            if item.get("user") != key or int(item.get("v") or 0) != ver:
                continue
            item_sid = str(item.get("sid") or "")
            if sid:
                if item_sid != sid:
                    continue
            elif item_sid:
                continue
            item["exp"] = new_exp
            item["sid"] = new_sid
            item["v"] = ver
            found = True
            break
        if not found:
            return None, 0
        _store_sessions(items)
    token = _sign_claims({"u": key, "v": ver, "exp": new_exp, "sid": new_sid})
    return token, new_exp


def session_expires_in(user):
    """Seconds until this browser login expires."""
    claims = user.get("_session") if isinstance(user, dict) else None
    if not isinstance(claims, dict):
        return 0
    exp = session_row_exp(user.get("username_key") or "", claims)
    return max(0, int(exp - time.time()))


def drop_session(user):
    """Remove this browser's login row so the cookie cannot be used again."""
    claims = user.get("_session") if isinstance(user, dict) else None
    if not isinstance(claims, dict):
        return
    sid = str(claims.get("sid") or "")
    exp = int(claims.get("exp") or 0)
    key = user.get("username_key")
    ver = int(claims.get("v") or 0)
    with _lock:
        kept = []
        for item in _load_sessions():
            if sid and item.get("sid") == sid:
                continue
            if not sid and item.get("user") == key and int(item.get("exp") or 0) == exp and int(item.get("v") or 0) == ver:
                continue
            kept.append(item)
        _store_sessions(kept)


def signed_in_names():
    """Usernames whose session row is still in the future. Shown as online."""
    now = time.time()
    with _lock:
        users = {}
        for user in _read()["users"]:
            if user.get("username_key"):
                users[user["username_key"]] = user
        kept = []
        names = []
        seen = set()
        items = _load_sessions()
        for item in items:
            user = users.get(item.get("user"))
            if not user or not user.get("password"):
                continue
            if int(item.get("v") or 0) != int(user.get("session_version") or 1):
                continue
            if int(item.get("exp") or 0) < now:
                continue
            kept.append(item)
            name = user.get("username") or ""
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if kept != items:
            _store_sessions(kept)
        return names


def _session_key():
    """Create or load the HMAC key. It is not the message key and is not sent to browsers."""
    try:
        key = open(SESSION_PATH, "rb").read()
    except OSError:
        key = b""
    if len(key) < 32:
        key = secrets.token_bytes(32)
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = SESSION_PATH + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, SESSION_PATH)
    return key


def hash_password(password):
    """scrypt the password. The salt and parameters are stored beside the hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32)
    return {"salt": salt.hex(), "hash": digest.hex(), "n": SCRYPT_N, "r": 8, "p": 1}


def password_matches(password, record):
    """Check a password. The work factors are capped so a stored row cannot stall the process."""
    if not isinstance(password, str) or not password or len(password) > 200:
        return False
    if not isinstance(record, dict):
        return False
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        n = int(record.get("n") or SCRYPT_N)
        r = int(record.get("r") or 8)
        p = int(record.get("p") or 1)
    except (KeyError, ValueError):
        return False
    if n > 2 ** 16 or r > 16 or p > 4:
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


def _public(user):
    """Account fields a signed-in admin list may show. The password hash is not included."""
    return {
        "username": user["username"],
        "email": user.get("email") or "",
        "role": user.get("role") or "member",
        "ready": bool(user.get("password")),
    }


def find_user(username):
    """Look up an account by username. The match is case-insensitive."""
    key = (username or "").strip().lower()
    with _lock:
        for user in _read()["users"]:
            if user.get("username_key") == key:
                return user
    return None


def find_user_by_email(email):
    """Look up an account by email. The address is not printed by callers."""
    email = (email or "").strip().lower()
    if not email:
        return None
    with _lock:
        for user in _read()["users"]:
            if (user.get("email") or "").lower() == email and user.get("password"):
                return user
    return None


def _prune_invites(data):
    """Expired invites are kept so the owner can see and resend them. Expired links are
    still refused by accept_invite, and invite_pending/pending_invite_count skip them.
    Returns False: nothing is removed."""
    return False


def invite_pending(email):
    """True when this email already has an unused invite that has not expired."""
    email = (email or "").strip().lower()
    if not email:
        return False
    now = time.time()
    with _lock:
        for item in _read()["invites"]:
            if (item.get("email") or "").lower() == email and int(item.get("expires") or 0) >= now:
                return True
    return False


def pending_invite_count():
    """How many unexpired invites are still waiting. Used to avoid unbounded invite lists."""
    now = time.time()
    with _lock:
        return len([item for item in _read()["invites"] if int(item.get("expires") or 0) >= now])


def invite_email(token):
    """Invite without email: the email an older invite was sent to (or ""), so the new-account page can fill it in."""
    if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
        return ""
    digest = _token_hash(token)
    with _lock:
        for item in _read()["invites"]:
            if _same_hash(item.get("token_hash") or "", digest):
                return item.get("email") or ""
    return ""


def invite_inviter(token):
    """Username key of whoever sent this invite, or "" for old invites and bad tokens."""
    if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
        return ""
    digest = _token_hash(token)
    with _lock:
        for item in _read()["invites"]:
            if _same_hash(item.get("token_hash") or "", digest):
                return (item.get("invited_by") or "").strip().lower()
    return ""


def resend_invite(email, requester_key, is_admin=False, invite_id=""):
    """Replace the token of a pending invite so the old link stops working. Returns (token, role, error).
    Invite without email: an invite is found by its id when one is given (invites may have no email), else by email."""
    email = (email or "").strip().lower()
    invite_id = invite_id.strip() if isinstance(invite_id, str) else ""
    requester_key = (requester_key or "").strip().lower()
    if not email and not invite_id:
        return None, "", "Choose the invite to resend."
    with _lock:
        data = _read()
        _prune_invites(data)
        invite = None
        for item in data["invites"]:
            if (invite_id and item.get("id") == invite_id) or (not invite_id and (item.get("email") or "").lower() == email):
                invite = item
                break
        if invite is None:
            _write(data)
            return None, "", "That invite is no longer waiting. Make a new one."
        if not is_admin and (invite.get("invited_by") or "") != requester_key:
            return None, "", "Only the person who sent that invite can resend it."
        token = secrets.token_urlsafe(32)
        _bury(data, invite.get("token_hash"), "replaced")
        invite["token_hash"] = _token_hash(token)
        invite["expires"] = int(time.time()) + INVITE_SECONDS
        role = invite.get("role") if invite.get("role") in ("admin", "member") else "member"
        _write(data)
    return token, role, ""


def sender_header(user):
    """From line for mail. An account whose email is not the house mailbox may fail at Gmail. Callers say the mail did not send."""
    email = (user.get("email") or "").strip()
    name = " ".join((user.get("username") or "").split())
    if not name or any(ch in name for ch in "\r\n<>"):
        return ""
    if not EMAIL_RE.match(email) or len(email) > 200 or any(ch in email for ch in "\r\n"):
        return ""
    return "%s <%s>" % (name, email)


def authenticate(username, password):
    """Return the account when the username and password match, else None."""
    user = find_user(username)
    if not user or not password_matches(password, user.get("password")):
        return None
    if user.get("disabled"):
        return None
    return user


def make_session(user):
    """Start a 14-day login and return the cookie token."""
    claims = {
        "u": user["username_key"],
        "v": int(user.get("session_version") or 1),
        "exp": int(time.time()) + SESSION_SECONDS,
        "sid": secrets.token_hex(16),
    }
    touch_session(user, claims)
    return _sign_claims(claims)


def _cookie_value(header, name):
    """Read one cookie from a Cookie header."""
    if not isinstance(header, str) or not header:
        return ""
    prefix = name + "="
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part.split("=", 1)[1].strip()
    return ""


def _open_claims(token):
    """Check a cookie signature and return the claims, or None."""
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    body, sig = token.split(".", 1)
    try:
        payload = base64.urlsafe_b64decode(body + "=" * ((4 - len(body) % 4) % 4))
        got = base64.urlsafe_b64decode(sig + "=" * ((4 - len(sig) % 4) % 4))
    except (ValueError, TypeError):
        return None
    expected = hmac.new(_session_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(got, expected):
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp < time.time():
        return None
    return data


def make_marked_token(user, kind, seconds, extra=None):
    """A short-lived signed token for a code, a finish step, or the verification cookie."""
    claims = {
        "u": user["username_key"],
        "v": int(user.get("session_version") or 1),
        "exp": int(time.time()) + int(seconds),
        "kind": kind,
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key not in claims:
                claims[key] = value
    return _sign_claims(claims)


def verify_cookie_ok(header, user):
    """True when this browser's verification cookie still matches the account and session version."""
    data = _open_claims(_cookie_value(header, "huzz_chat_verified"))
    if not data or data.get("kind") != "verify":
        return False
    if data.get("u") != user.get("username_key"):
        return False
    try:
        version = int(data.get("v") or 0)
    except (TypeError, ValueError):
        return False
    return version == int(user.get("session_version") or 1)


def open_marked_cookie(header, name, kind, user):
    """0.23.3-beta.15: claims of a signed cookie of this kind for this account and session version, or None.
    Used by the Administrator page's short admin session cookie."""
    data = _open_claims(_cookie_value(header, name))
    if not data or data.get("kind") != kind or data.get("u") != user.get("username_key"):
        return None
    try:
        if int(data.get("v") or 0) != int(user.get("session_version") or 1):
            return None
    except (TypeError, ValueError):
        return None
    return data


def open_login_ticket(token, kind):
    """Open a login-code or finish token and return that account, or None when it is stale."""
    data = _open_claims(token if isinstance(token, str) else "")
    if not data or data.get("kind") != kind:
        return None
    found = find_user(data.get("u") or "")
    if not found or not found.get("password"):
        return None
    if int(found.get("session_version") or 1) != int(data.get("v") or 0):
        return None
    return {
        "username": found["username"],
        "username_key": found["username_key"],
        "email": found.get("email") or "",
        "role": found.get("role") or "member",
        "session_version": int(found.get("session_version") or 1),
        "_ticket": data,
    }


def user_from_cookie(header):
    """The account for a still-valid session cookie. Passive reads do not extend the login."""
    data = _open_claims(_cookie_value(header, "huzz_chat_session"))
    if not data or data.get("kind"):
        return None
    user = find_user(data.get("u") or "")
    if not user or int(user.get("session_version") or 1) != int(data.get("v") or 0):
        return None
    if not user.get("password"):
        return None
    if user.get("disabled"):
        return None
    claims = {
        "sid": str(data.get("sid") or ""),
        "exp": int(data.get("exp") or 0),
        "v": int(data.get("v") or 0),
    }
    if not session_known(user["username_key"], claims):
        return None
    return {
        "username": user["username"],
        "username_key": user["username_key"],
        "email": user.get("email") or "",
        "role": user.get("role") or "member",
        "session_version": int(user.get("session_version") or 1),
        "_session": claims,
    }


def list_users():
    """Accounts without password hashes, for the admin invite screen."""
    with _lock:
        data = _read()
        if _prune_invites(data):
            _write(data)
        rows = []
        for user in data["users"]:
            row = _public(user)
            if not user.get("password"):
                row["pending"] = True
            rows.append(row)
        taken = _account_emails(data)
        for invite in data["invites"]:
            if invite.get("username_key"):
                continue
            if (invite.get("email") or "").strip().lower() in taken:
                continue  # 26.10.10f: already an account
            rows.append({
                "username": "",
                "email": invite.get("email") or "",
                "role": invite.get("role") or "member",
                "ready": False,
                "pending": True,
                "invite_id": invite.get("id") or "",
            })
        return rows


def change_password(username, current, new_password):
    """Replace the password and bump session_version so older cookies and the verification cookie stop working."""
    if not isinstance(new_password, str) or len(new_password) < 8 or len(new_password) > 200:
        return "Use at least 8 characters."
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != (username or "").lower():
                continue
            if not password_matches(current, user.get("password")):
                return "The current password was not accepted."
            user["password"] = hash_password(new_password)
            user["session_version"] = int(user.get("session_version") or 1) + 1
            _forget_user_sessions(user["username_key"])
            _write(data)
            return ""
    return "That account was not found."


def set_password(username, new_password):
    """Set a password from a reset code. Older sessions stop. The caller removes the sealed key backup."""
    if not isinstance(new_password, str) or len(new_password) < 8 or len(new_password) > 200:
        return "Use at least 8 characters."
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != (username or "").lower():
                continue
            if not user.get("password"):
                return "That account was not found."
            user["password"] = hash_password(new_password)
            user["session_version"] = int(user.get("session_version") or 1) + 1
            _forget_user_sessions(user["username_key"])
            _write(data)
            return ""
    return "That account was not found."


def hotp(key, counter, digits=6):
    """RFC 4226 HOTP. TOTP is this with the 30-second counter."""
    msg = int(counter).to_bytes(8, "big")
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return "%0*d" % (digits, binary % (10 ** digits))


def _b32_key(secret):
    """Decode an authenticator secret. Apps show it without padding."""
    text = "".join(ch for ch in (secret or "").upper() if ch.isalnum())
    if not text or len(text) > 128:
        raise ValueError("secret")
    pad = "=" * ((8 - len(text) % 8) % 8)
    return base64.b32decode(text + pad, casefold=True)


def totp_accept(secret, digits, last_step, now=None):
    """Return the step when digits match the current, previous, or next code, else None.

    last_step is the last accepted counter, so the same code cannot be used twice.
    """
    if not re.fullmatch(r"\d{6}", digits or ""):
        return None
    try:
        key = _b32_key(secret)
    except (ValueError, TypeError):
        return None
    moment = time.time() if now is None else now
    base = int(moment // 30)
    floor = int(last_step or 0)
    for counter in (base - 1, base, base + 1):
        if counter <= floor:
            continue
        if hmac.compare_digest(hotp(key, counter), digits):
            return counter
    return None


def totp_enabled(user):
    """True when sign-in must ask for an authenticator or backup code after the password."""
    totp = user.get("totp") if isinstance(user, dict) else None
    return isinstance(totp, dict) and bool(totp.get("enabled")) and bool(totp.get("secret"))


def _norm_factor(typed):
    """Drop spaces and dashes. Authenticator codes are 6 digits. Backup codes are 8 letters."""
    if not isinstance(typed, str):
        return ""
    return "".join(ch for ch in typed.upper() if ch.isalnum())


def _fresh_backup_codes():
    """Ten single-use codes. The caller stores hashes and shows these once."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    shown = []
    for _ in range(10):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        shown.append(raw[:4] + "-" + raw[4:])
    return shown


def begin_totp(username):
    """Save a secret that does nothing until confirm_totp. It expires in 10 minutes."""
    key = (username or "").strip().lower()
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            if totp_enabled(user):
                return "", "Two-factor authentication is already on."
            user["totp_pending"] = {"secret": secret, "exp": int(time.time()) + 600}
            _write(data)
            return secret, ""
    return "", "That account was not found."


def confirm_totp(username, code):
    """Turn two-factor on after one matching authenticator code. Returns the backup codes once."""
    key = (username or "").strip().lower()
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            if totp_enabled(user):
                return None, "Two-factor authentication is already on."
            pending = user.get("totp_pending") if isinstance(user.get("totp_pending"), dict) else {}
            secret = pending.get("secret") or ""
            if not secret or int(pending.get("exp") or 0) < time.time():
                user.pop("totp_pending", None)
                _write(data)
                return None, "Start setup again. That secret expired."
            counter = totp_accept(secret, digits, 0)
            if counter is None:
                return None, "That code was not accepted."
            shown = _fresh_backup_codes()
            user["totp"] = {
                "enabled": True,
                "secret": secret,
                "last_step": counter,
                "backup": [code_hash(key, "backup:" + item.replace("-", "")) for item in shown],
            }
            user.pop("totp_pending", None)
            _write(data)
            return shown, ""
    return None, "That account was not found."


def cancel_totp_pending(username):
    """Drop a secret that was never confirmed."""
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            if "totp_pending" in user:
                user.pop("totp_pending", None)
                _write(data)
            return ""
    return ""


def consume_factor(username, typed, allow_backup=True):
    """Accept a live authenticator code or, when allowed, one unused backup code."""
    key = (username or "").strip().lower()
    norm = _norm_factor(typed)
    if not norm or len(norm) > 16:
        return False
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            totp = user.get("totp") if isinstance(user.get("totp"), dict) else None
            if not totp_enabled(user):
                return False
            if len(norm) == 6 and norm.isdigit():
                counter = totp_accept(totp.get("secret"), norm, int(totp.get("last_step") or 0))
                if counter is None:
                    return False
                totp["last_step"] = counter
                user["totp"] = totp
                _write(data)
                return True
            if not allow_backup or len(norm) != 8:
                return False
            hashes = totp.get("backup") if isinstance(totp.get("backup"), list) else []
            target = code_hash(key, "backup:" + norm)
            kept = []
            found = False
            for item in hashes:
                if not isinstance(item, str):
                    continue
                try:
                    matched = (not found) and hmac.compare_digest(item, target)
                except ValueError:
                    matched = False
                if matched:
                    found = True
                    continue
                kept.append(item)
            if not found:
                return False
            totp["backup"] = kept
            user["totp"] = totp
            _write(data)
            return True
    return False


def disable_totp(username, code):
    """Turn two-factor off. Requires a current authenticator code or a backup code."""
    if not consume_factor(username, code, allow_backup=True):
        return "That code was not accepted."
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            user.pop("totp", None)
            user.pop("totp_pending", None)
            _write(data)
            return ""
    return "That account was not found."


def replace_backup_codes(username, code):
    """Mint new backup codes. A current authenticator code is required, not a backup code."""
    if not consume_factor(username, code, allow_backup=False):
        return None, "Enter a current authenticator code."
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        for user in data["users"]:
            if user.get("username_key") != key:
                continue
            totp = user.get("totp") if isinstance(user.get("totp"), dict) else None
            if not totp_enabled(user):
                return None, "Two-factor authentication is off."
            shown = _fresh_backup_codes()
            totp["backup"] = [code_hash(key, "backup:" + item.replace("-", "")) for item in shown]
            user["totp"] = totp
            _write(data)
            return shown, ""
    return None, "That account was not found."


def _token_hash(token):
    """Hash an invite token. The raw token is only in the email link."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invite(email, role, invited_by=""):
    """Store a hashed invite with the inviter's username key. The role is admin or member. Returns the raw token once.
    Invite without email: the email is optional. With no email the invite is a plain shareable link (code or QR),
    and the person who opens it types their own email on the new-account page. The "already has an account" and
    "replace the older invite for this email" checks only run when an email is given."""
    email = email.strip() if isinstance(email, str) else ""
    if email and (not EMAIL_RE.match(email) or len(email) > 200 or "\n" in email or "\r" in email):
        return None, "Enter a real email address."
    if role not in ("admin", "member"):
        return None, "Choose an admin account or a limited account."
    token = secrets.token_urlsafe(32)
    with _lock:
        data = _read()
        _prune_invites(data)
        if email:
            for user in data["users"]:
                if (user.get("email") or "").lower() == email.lower() and user.get("password"):
                    return None, "That email already has an account."
            for item in data["invites"]:
                if (item.get("email") or "").lower() == email.lower():
                    _bury(data, item.get("token_hash"), "replaced")
            data["invites"] = [
                item for item in data["invites"]
                if (item.get("email") or "").lower() != email.lower()
            ]
        data["invites"].append({
            "id": uuid.uuid4().hex,
            "email": email,
            "role": role,
            "token_hash": _token_hash(token),
            "expires": int(time.time()) + INVITE_SECONDS,
            "invited_by": (invited_by or "").strip().lower(),
            # When it was made, so an invite with no email can still be labelled in "Pending invites".
            "created": int(time.time()),
        })
        _write(data)
    return token, ""


def _same_hash(stored, digest):
    """Constant-time compare of a stored hash and a new digest."""
    if not isinstance(stored, str) or len(stored) != len(digest):
        return False
    return hmac.compare_digest(stored, digest)


GONE_KEEP = 14 * 86400


def _bury(data, token_hash, state, by=""):
    """0.23.3: remember why a token stopped working (used / revoked / replaced) for 14 days, so the invite pages can
    say so instead of a generic error. Only the token hash is kept."""
    if not token_hash:
        return
    now = int(time.time())
    rows = [r for r in data.get("invite_gone", []) if now - int(r.get("at") or 0) < GONE_KEEP and r.get("h") != token_hash]
    row = {"h": token_hash, "s": state, "at": now}
    if by:
        row["by"] = by  # 26.10.2: who cancelled it (username key), for the record
    rows.append(row)
    data["invite_gone"] = rows[-500:]


def invite_state(token):
    """('valid' | 'expired' | 'used' | 'revoked' | 'invalid', inviter key, expires). Read-only."""
    if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
        return "invalid", "", 0
    digest = _token_hash(token)
    now = time.time()
    with _lock:
        data = _read()
        for item in data["invites"]:
            if _same_hash(item.get("token_hash") or "", digest):
                exp = int(item.get("expires") or 0)
                return ("valid" if exp >= now else "expired"), (item.get("invited_by") or ""), exp
        for row in data.get("invite_gone", []):
            if _same_hash(row.get("h") or "", digest):
                return ("used" if row.get("s") == "used" else "revoked"), "", 0
    return "invalid", "", 0


GONE_WORDS = {
    "used": "This invite was already used. Sign in instead, or ask for a new invite.",
    "revoked": "This invite was cancelled. Ask for a new one.",
    "expired": "That link has expired. Ask for a new one.",
    "invalid": "That link is not valid.",
}


def _account_emails(data):
    """26.10.10f: lowercase emails of finished accounts (ones with a password)."""
    return {(u.get("email") or "").strip().lower() for u in data.get("users", []) if u.get("password") and (u.get("email") or "").strip()}


def _settle_invites_for(data, email, username_key, keep=None):
    """26.10.10f: once an account owns an email, every other waiting invite for that email (any letter case) is
    marked accepted: moved to "invites_accepted" (kept for the record, never deleted) and its link stops working.
    Returns how many were settled."""
    email = (email or "").strip().lower()
    if not email:
        return 0
    settled = []
    kept = []
    for item in data.get("invites", []):
        if item is not keep and not item.get("username_key") and (item.get("email") or "").strip().lower() == email:
            settled.append(item)
        else:
            kept.append(item)
    if not settled:
        return 0
    data["invites"] = kept
    now = int(time.time())
    rows = data.setdefault("invites_accepted", [])
    for item in settled:
        _bury(data, item.get("token_hash"), "used")
        rows.append({"id": item.get("id") or "", "email": item.get("email") or "", "role": item.get("role") or "member",
                     "invited_by": item.get("invited_by") or "", "created": item.get("created") or 0,
                     "accepted_by": username_key, "at": now})
    data["invites_accepted"] = rows[-1000:]
    return len(settled)


def settle_stale_invites(dry_run=True):
    """26.10.10f one-time cleanup: invites whose email (any case) already belongs to an account are marked accepted.
    Returns [(invite_id, invite_email, username)]. dry_run=True changes nothing."""
    with _lock:
        data = _read()
        owners = {}
        for u in data.get("users", []):
            e = (u.get("email") or "").strip().lower()
            if e and u.get("password"):
                owners.setdefault(e, u)
        found = []
        for item in data.get("invites", []):
            e = (item.get("email") or "").strip().lower()
            if e in owners and not item.get("username_key"):
                found.append((item.get("id") or "", item.get("email") or "", owners[e].get("username") or ""))
        if found and not dry_run:
            for e in {f[1].strip().lower() for f in found}:
                _settle_invites_for(data, e, owners[e].get("username_key") or "")
            _write(data)
        return found


def accept_invite(token, username, new_password, email=None):
    """Turn an invite into an account. The link stops working after this.
    Invite without email: the new-account page sends the email the person typed, and it is required. It must be a real
    address that no other account uses, and the new account gets THAT email (not the one the invite was sent to).
    email=None is only for the command-line tools (manage.py), which keep the invite's own email."""
    if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
        return "That link is not valid."
    username = username.strip() if isinstance(username, str) else ""
    if not USERNAME_RE.match(username):
        return "Usernames are 3 to 32 letters, numbers, dots, or dashes."
    if NAME_CHECK is not None:
        refused = NAME_CHECK(username)
        if refused:
            return refused
    if not isinstance(new_password, str) or len(new_password) < 8 or len(new_password) > 200:
        return "Use at least 8 characters."
    if email is not None:
        email = email.strip() if isinstance(email, str) else ""
        if not email:
            return "Enter your email address."
        if not EMAIL_RE.match(email) or len(email) > 200 or "\n" in email or "\r" in email:
            return "Enter a real email address."
    digest = _token_hash(token)
    now = time.time()
    key = username.lower()
    with _lock:
        data = _read()
        invite = None
        for item in data["invites"]:
            if _same_hash(item.get("token_hash") or "", digest):
                invite = item
                break
        if not invite:
            for row in data.get("invite_gone", []):
                if _same_hash(row.get("h") or "", digest):
                    return GONE_WORDS["used" if row.get("s") == "used" else "revoked"]
            return GONE_WORDS["invalid"]
        if int(invite.get("expires") or 0) < now:
            return GONE_WORDS["expired"]
        reserved = invite.get("username_key") or ""
        if reserved and reserved != key:
            return "That invite is already tied to another username."
        if email is not None:
            # The typed email must not belong to a different account (the reserved account itself may keep its own).
            for user in data["users"]:
                if (user.get("email") or "").lower() == email.lower() and user.get("username_key") != reserved:
                    return "That email already has an account. Sign in instead, or use a different email."
        for user in data["users"]:
            if user.get("username_key") == key and user.get("password"):
                return "That username is already used."
            if reserved and user.get("username_key") == reserved:
                user["password"] = hash_password(new_password)
                if email is not None:
                    user["email"] = email
                user["role"] = invite.get("role") or user.get("role") or "member"
                user["session_version"] = int(user.get("session_version") or 1) + 1
                data["invites"] = [item for item in data["invites"] if item is not invite]
                _bury(data, invite.get("token_hash"), "used")
                _settle_invites_for(data, user.get("email"), key)
                _settle_invites_for(data, invite.get("email"), key)
                _write(data)
                return ""
        if any(user.get("username_key") == key for user in data["users"]):
            return "That username is already used."
        role = invite.get("role") if invite.get("role") in ("admin", "member") else "member"
        data["users"].append({
            "id": uuid.uuid4().hex,
            "username": username,
            "username_key": key,
            "email": email if email is not None else (invite.get("email") or ""),
            "role": role,
            "password": hash_password(new_password),
            "session_version": 1,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        data["invites"] = [item for item in data["invites"] if item is not invite]
        _bury(data, invite.get("token_hash"), "used")
        # 26.10.10f: other invites for this email (or the one the invite was sent to) are now accepted too.
        _settle_invites_for(data, data["users"][-1]["email"], key)
        _settle_invites_for(data, invite.get("email"), key)
        _write(data)
    return ""


def pending_invites(sent_by=None):
    """26.10.2: unused invites for the "Pending invites" list: id, email, role, who sent it, when it expires.
    sent_by=None lists everyone's (admins and the owner); a username key lists only that person's own invites,
    because a member may only see and cancel invites they sent themselves."""
    now = int(time.time())
    rows = []
    with _lock:
        data = _read()
        taken = _account_emails(data)
        for item in data["invites"]:
            if item.get("username_key"):
                continue  # an invite already turned into an account that is still being finished
            if (item.get("email") or "").strip().lower() in taken:
                continue  # 26.10.10f: that email already has an account, so the invite is not pending
            by = (item.get("invited_by") or "").strip().lower()
            if sent_by is not None and by != sent_by:
                continue
            exp = int(item.get("expires") or 0)
            # Invite without email: "created" lets the app label an invite that has no email ("Invite link made 3:10 PM").
            # Older invites have no "created"; their start is worked out from the 72-hour lifetime.
            rows.append({"id": item.get("id") or "", "email": item.get("email") or "", "role": item.get("role") or "member",
                         "invited_by": by, "expires": exp, "expired": exp < now,
                         "created": int(item.get("created") or (exp - INVITE_SECONDS))})
    rows.sort(key=lambda r: -r["expires"])
    return rows


def invite_sender(invite_id):
    """26.10.2: (found, sender username key) for one unused invite. Used to check who may cancel it."""
    invite_id = (invite_id or "").strip() if isinstance(invite_id, str) else ""
    if not invite_id:
        return False, ""
    with _lock:
        for item in _read()["invites"]:
            if item.get("id") == invite_id and not item.get("username_key"):
                return True, (item.get("invited_by") or "").strip().lower()
    return False, ""


def cancel_invite(invite_id, by=""):
    """Delete one unused invite by id. Its link stops working at once: the token hash is remembered as "revoked",
    so the invite page says "This invite was cancelled" instead of a generic error.
    26.10.2: by = username key of whoever cancelled it, kept with that record (who cancelled, never the email)."""
    invite_id = (invite_id or "").strip() if isinstance(invite_id, str) else ""
    if not invite_id:
        return "That invite was not found."
    with _lock:
        data = _read()
        kept = [item for item in data["invites"] if item.get("id") != invite_id]
        if len(kept) == len(data["invites"]):
            return "That invite was not found."
        for item in data["invites"]:
            if item.get("id") == invite_id:
                _bury(data, item.get("token_hash"), "revoked", by)
        data["invites"] = kept
        _write(data)
    return ""


def remove_user(username):
    """Delete the account record. The caller also drops devices, friends, push, and the key backup."""
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        target = next((user for user in data["users"] if user.get("username_key") == key), None)
        if not target:
            return "That account was not found."
        if target.get("role") == "admin":
            admins = [user for user in data["users"] if user.get("role") == "admin" and user.get("password")]
            if len(admins) < 2:
                return "The last owner account stays."
        data["users"] = [user for user in data["users"] if user.get("username_key") != key]
        data["invites"] = [item for item in data["invites"] if item.get("username_key") != key]
        _forget_user_sessions(key)
        _write(data)
    return ""


def send_invite(email, link, role, invited_by="", reply_to=""):
    """Email the invite link from the house mailbox, showing the inviter's name and replying to them. Failure is returned to the page."""
    invited_by = " ".join((invited_by or "").split())
    if any(ch in invited_by for ch in "\r\n"):
        invited_by = ""
    if role == "admin":
        access = "This is an admin account. It can invite other people."
    else:
        access = "This is a member account. It can message people and invite friends."
    place = ("%s on %s" % (INSTANCE_NAME, EMAIL_BRAND)) if INSTANCE_NAME else EMAIL_BRAND
    if invited_by:
        lead = "%s invited you to %s." % (invited_by, place)
        subject = "%s invited you to %s" % (invited_by, place)
    else:
        lead = "You are invited to %s." % place
        subject = "Your %s invite" % place
    note = (" " + bcconfig.INVITE_NOTE) if bcconfig.INVITE_NOTE else ""
    body = (
        "%s\n\n"
        "Open this link to choose your username and password. "
        "It works once and lasts 72 hours:\n%s\n\n"
        "This account is only for %s.%s\n\n"
        "%s\n\n"
        "After that, sign in at %s\n"
    ) % (lead, link, PUBLIC_URL, note, access, PUBLIC_URL)
    house = parseaddr(MAIL_FROM)[1]
    from_header = MAIL_FROM
    if invited_by and house and not any(ch in invited_by for ch in "<>\"\r\n"):
        from_header = formataddr((invited_by + " via " + EMAIL_BRAND, house))
    reply = (reply_to or "").strip()
    extra = ""
    if reply and EMAIL_RE.match(reply) and len(reply) <= 200 and not any(ch in reply for ch in "\r\n<>,"):
        extra = "Reply-To: %s\n" % reply
    # 26.10.2 light emails: same plain text as before, plus the HTML version with the real group,
    # inviter, invite lifetime and link. Both go in one message (see compose_mail).
    page = mailtemplates.invite(email, link, subject, invited_by, INVITE_SECONDS // 3600, access, bcconfig.INVITE_NOTE)
    message = compose_mail(email, from_header, subject, body, page, reply_to=extra[len("Reply-To: "):].strip())
    return deliver_mail(message)


def compose_mail(to, from_header, subject, text, html_page, reply_to=""):
    """Build one email with two versions inside (multipart/alternative): the plain text first, then the HTML.
    Mail apps show the HTML when they can and fall back to the plain text when they can't.
    Both parts are quoted-printable, so long HTML lines are wrapped safely for sendmail/ssmtp (Gmail) and SMTP
    (Postmark). Header lines are not folded, so deliver_mail can still read To/From line by line.
    Returns the whole message as text (headers, blank line, body), the same shape deliver_mail always took."""
    from email.message import EmailMessage
    from email.policy import SMTP as _smtp_policy
    # Body lines are wrapped at 76 characters (the quoted-printable rule); headers are written unfolded (998).
    body_policy = _smtp_policy.clone(linesep="\n", max_line_length=76)
    head_policy = _smtp_policy.clone(linesep="\n", max_line_length=998)
    try:
        msg = EmailMessage(policy=body_policy)
        msg["To"] = to
        msg["From"] = from_header
        if reply_to:
            msg["Reply-To"] = reply_to
        msg["Subject"] = subject
        msg.set_content(text, subtype="plain", charset="utf-8", cte="quoted-printable")
        msg.add_alternative(html_page, subtype="html", charset="utf-8", cte="quoted-printable")
        for part in msg.iter_parts():
            del part["MIME-Version"]  # only the outer message needs this header
        return msg.as_string(policy=head_policy)
    except Exception:  # an odd From/To that the email library refuses: send the plain text exactly as before
        extra = ("Reply-To: %s\n" % reply_to) if reply_to else ""
        return ("To: %s\nFrom: %s\n%sSubject: %s\nContent-Type: text/plain; charset=utf-8\n\n%s"
                % (to, from_header, extra, subject, text))


def deliver_mail(message):
    """Send one message (headers, blank line, body). Since 26.10.2 the body is multipart (plain text + HTML).

    BC_MAIL_MODE picks the transport:
      sendmail  (default, as before) pipe to BC_SENDMAIL, default /usr/sbin/sendmail
      smtp      BC_SMTP_HOST / BC_SMTP_PORT / BC_SMTP_USER / BC_SMTP_PASSWORD(_FILE) / BC_SMTP_TLS
      log       print the message to the server log instead of sending (testing, or no mail server)
    Returns True when the message was handed off.
    """
    mode = bcconfig.MAIL_MODE
    if mode == "log":
        print("[branch-chat mail, BC_MAIL_MODE=log, not sent]\n%s\n[end of mail]" % message, flush=True)
        return True
    if mode == "smtp":
        return _deliver_smtp(message)
    try:
        result = subprocess.run(
            [bcconfig.SENDMAIL, "-t", "-oi"],
            input=message.encode("utf-8"),
            capture_output=True,
            timeout=40,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _deliver_smtp(message):
    """SMTP with STARTTLS (587), implicit TLS (465) or none (BC_SMTP_TLS=none, LAN relays only)."""
    import smtplib
    import ssl
    head, _sep, _body = message.partition("\n\n")
    headers = {}
    for line in head.split("\n"):
        key, _c, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    sender = parseaddr(headers.get("from", ""))[1] or parseaddr(MAIL_FROM)[1]
    rcpt = parseaddr(headers.get("to", ""))[1]
    if not sender or not rcpt:
        return False
    domain = sender.rpartition("@")[2] or None
    extra = "Date: %s\nMessage-ID: %s\n" % (formatdate(localtime=False), make_msgid(domain=domain))
    data = (extra + message).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    host, port, tls = bcconfig.SMTP_HOST, bcconfig.SMTP_PORT, bcconfig.SMTP_TLS
    if not host:
        return False
    try:
        context = ssl.create_default_context()
        if tls == "ssl":
            conn = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
        else:
            conn = smtplib.SMTP(host, port, timeout=30)
        with conn:
            if tls == "starttls":
                conn.starttls(context=context)
            if bcconfig.SMTP_USER:
                conn.login(bcconfig.SMTP_USER, bcconfig.smtp_password())
            conn.sendmail(sender, [rcpt], data)
    except (OSError, smtplib.SMTPException, ssl.SSLError):
        return False
    return True


def code_hash(user_key, code):
    """Hash a 6-digit code with the account key so the file does not store the code."""
    return hmac.new(_session_key(), ("%s:%s" % (user_key, code)).encode("utf-8"), hashlib.sha256).hexdigest()


def send_reset_code(email, code, sender=""):
    """Email a password-reset code. Callers do not print the address."""
    if not EMAIL_RE.match(email or "") or any(ch in email for ch in "\r\n"):
        return False
    if not re.fullmatch(r"\d{6}", code or ""):
        return False
    subject = "Reset your %s password" % EMAIL_BRAND
    body = (
        "Your %s password reset code is %s.\n\n"
        "It expires in 10 minutes. If you did not ask to reset your password, "
        "you can ignore this email.\n\n"
        "Your message key backup was sealed with the old password, so it is removed. "
        "Sign in first on a device you already use and it is saved again with the new "
        "password. On a new device, older messages may not open.\n"
    ) % (EMAIL_BRAND, code)
    from_header = sender.strip() if isinstance(sender, str) else ""
    if not from_header or "\n" in from_header or "\r" in from_header:
        from_header = MAIL_FROM
    # 26.10.2 light emails: plain text above plus the HTML version (code box, "Resetting your password for" line).
    page = mailtemplates.reset_code(email, code, subject, CODE_MINUTES)
    return deliver_mail(compose_mail(email, from_header, subject, body, page))


def send_reset_link(email, username, link, minutes):
    """26.10.13.8: admin-sent reset email with the username and a one-time link. Callers do not print the address."""
    if not EMAIL_RE.match(email or "") or any(ch in email for ch in "\r\n"):
        return False
    if any(ch in (username or "") + link for ch in "\r\n"):
        return False
    subject = "Reset your %s password" % EMAIL_BRAND
    if os.environ.get("BC_TEST_SUBJECT_PREFIX"):
        subject = os.environ["BC_TEST_SUBJECT_PREFIX"] + " " + subject
    body = (
        "Hi %s,\n\n"
        "An administrator of %s sent you this so you can get back into your account.\n\n"
        "Your username is: %s\n\n"
        "Choose a new password with this link:\n%s\n\n"
        "The link works once and expires in %d minutes. "
        "If you didn't ask for this, you can ignore this email and your password stays the same.\n"
    ) % (username, EMAIL_BRAND, username, link, minutes)
    page = mailtemplates.reset_link(email, username, link, subject, minutes)
    return deliver_mail(compose_mail(email, MAIL_FROM, subject, body, page))


def send_code(email, code, sender=""):
    """Email a sign-in code. The address is the account's own address. Callers do not print it."""
    if not EMAIL_RE.match(email or "") or any(ch in email for ch in "\r\n"):
        return False
    if not re.fullmatch(r"\d{6}", code or ""):
        return False
    subject = "Your %s code" % EMAIL_BRAND
    body = (
        "Your %s code is %s.\n\n"
        "It expires in 10 minutes. If you did not try to sign in or open "
        "your messages, you can ignore this email.\n"
    ) % (EMAIL_BRAND, code)
    from_header = sender.strip() if isinstance(sender, str) else ""
    if not from_header or "\n" in from_header or "\r" in from_header:
        from_header = MAIL_FROM
    # 26.10.2 light emails: plain text above plus the HTML version (code box, "Signing in to" line).
    page = mailtemplates.sign_in_code(email, code, subject, CODE_MINUTES)
    return deliver_mail(compose_mail(email, from_header, subject, body, page))


def owner_user_rows():
    """Owner panel rows: accounts (no hashes) plus unused invites."""
    with _lock:
        data = _read()
        rows = []
        for user in data["users"]:
            rows.append({
                "username": user.get("username") or "",
                "key": user.get("username_key") or "",
                "email": user.get("email") or "",
                "role": user.get("role") or "member",
                "ready": bool(user.get("password")),
                "disabled": bool(user.get("disabled")),
            })
        taken = _account_emails(data)
        for invite in data["invites"]:
            if invite.get("username_key"):
                continue
            if (invite.get("email") or "").strip().lower() in taken:
                continue  # 26.10.10f: already an account
            expires = int(invite.get("expires") or 0)
            rows.append({"username": "", "key": "", "email": invite.get("email") or "",
                         "role": invite.get("role") or "member", "ready": False,
                         "disabled": False, "pending": True, "expires": expires,
                         "expired": expires < time.time(),
                         # 26.10.2: lets the Administrator page cancel this invite (the owner may cancel anyone's)
                         "invite_id": invite.get("id") or ""})
        return rows


def set_disabled(username, flag):
    """Reversible block. Disabling bumps the session version and drops every login row. Data stays."""
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        target = next((u for u in data["users"] if u.get("username_key") == key), None)
        if not target:
            return "That account was not found."
        if flag:
            target["disabled"] = True
            target["session_version"] = int(target.get("session_version") or 1) + 1
            _forget_user_sessions(key)
        else:
            target.pop("disabled", None)
        _write(data)
    return ""


def set_role(username, role):
    """Promote or demote. The last ready admin cannot be demoted."""
    if role not in ("admin", "member"):
        return "Pick admin or member."
    key = (username or "").strip().lower()
    with _lock:
        data = _read()
        target = next((u for u in data["users"] if u.get("username_key") == key), None)
        if not target:
            return "That account was not found."
        if role == "member" and target.get("role") == "admin":
            admins = [u for u in data["users"] if u.get("role") == "admin" and u.get("password")]
            if len(admins) < 2:
                return "The last admin stays."
        target["role"] = role
        _write(data)
    return ""


def disabled_keys():
    """Account keys with the disabled flag. Read at request time; nothing is migrated."""
    with _lock:
        return {u.get("username_key") for u in _read()["users"] if u.get("disabled") and u.get("username_key")}
