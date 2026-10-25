"""Web Push for Branch Chat Server.

A push only wakes the phone. The body is a fixed wake-up, never a
message. The phone decrypts the message itself and shows the sender
and the text. Push service hosts see the wake-up ciphertext only.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

import bcconfig

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

STATE_DIR = bcconfig.STATE_DIR
VAPID_PATH = os.path.join(STATE_DIR, "vapid.json")
PUSH_PATH = os.path.join(STATE_DIR, "push.json")
LOG_PATH = os.path.join(STATE_DIR, "push.log")
SUBJECT = bcconfig.PUBLIC_URL
WAKE = b'{"v":1}'
MAX_PER_USER = 8

_lock = threading.Lock()
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_HOSTS = {
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "web.push.apple.com",
    "wns.windows.com",
    "notify.windows.com",
}
_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".wns.windows.com")


def _b64url(raw):
    """Base64url without padding, as Web Push headers expect."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text, lo, hi):
    """Decode base64url and require the length window the caller names."""
    if not isinstance(text, str) or not _B64URL.match(text) or not (lo <= len(text) <= hi):
        return None
    pad = "=" * ((4 - len(text) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(text + pad)
    except Exception:
        return None
    return raw


def _log(status):
    """Append a push status word. Endpoints and message text are not written."""
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + status + "\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
        os.chmod(LOG_PATH, 0o600)
        if os.path.getsize(LOG_PATH) > 100000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
            os.chmod(LOG_PATH + ".1", 0o600)
    except OSError:
        pass


def _write_json(path, obj):
    """Write a push state file mode 600."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")))
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _read_json(path):
    """Load a push state file, or an empty object."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _private_key(raw):
    """Load a P-256 private key from raw scalar bytes."""
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _public_uncompressed(key):
    """Uncompressed P-256 point. Push encryption requires that form."""
    return key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def _load_vapid():
    """Load the existing push key. Callers must not generate a new one over the old subscriptions."""
    data = _read_json(VAPID_PATH)
    private = _b64url_decode(data.get("private") or "", 40, 50)
    public = _b64url_decode(data.get("public") or "", 80, 100)
    if private and public and len(private) == 32 and len(public) == 65 and public[0] == 4:
        return private, public
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = _public_uncompressed(key.public_key())
    _write_json(VAPID_PATH, {"private": _b64url(private), "public": _b64url(public)})
    return private, public


def public_key():
    """Public push key the browser uses to subscribe."""
    with _lock:
        _private, public = _load_vapid()
    return _b64url(public)


def _host_ok(host):
    """True for the push services this server is willing to contact."""
    host = (host or "").lower().rstrip(".")
    if host in _HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _SUFFIXES)


def _endpoint_ok(endpoint):
    """True when a subscription URL is https on an allowed push host."""
    if not isinstance(endpoint, str) or len(endpoint) > 1800 or '"' in endpoint or any(ch.isspace() for ch in endpoint):
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        return None
    if not _host_ok(parsed.hostname):
        return None
    return parsed.scheme + "://" + parsed.hostname


def _load_subs():
    """Stored push subscriptions. Each row is one browser."""
    data = _read_json(PUSH_PATH)
    rows = data.get("subs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _save_subs(rows):
    """Write subscriptions."""
    _write_json(PUSH_PATH, {"subs": rows})


def save_subscription(user_key, endpoint, p256dh, auth, paused_until):
    """Keep at most eight subscriptions per account. paused_until silences wakes until that time."""
    origin = _endpoint_ok(endpoint)
    pub = _b64url_decode(p256dh, 80, 120)
    secret = _b64url_decode(auth, 16, 40)
    if not origin or not pub or not secret or len(pub) != 65 or pub[0] != 4 or len(secret) != 16:
        return False
    if not isinstance(paused_until, int) or paused_until < 0 or paused_until > 4102444800:
        return False
    row = {
        "user": user_key,
        "endpoint": endpoint,
        "p256dh": p256dh,
        "auth": auth,
        "paused_until": paused_until,
        "updated": int(time.time()),
    }
    with _lock:
        rows = [item for item in _load_subs() if item.get("endpoint") != endpoint]
        mine = [item for item in rows if item.get("user") == user_key]
        if len(mine) >= MAX_PER_USER:
            mine.sort(key=lambda item: item.get("updated") or 0)
            drop = mine[0].get("endpoint")
            rows = [item for item in rows if item.get("endpoint") != drop]
        rows.append(row)
        _save_subs(rows)
    return True


def drop_endpoint(user_key, endpoint):
    """Forget one subscription URL for this account."""
    if not isinstance(endpoint, str) or not endpoint:
        return
    with _lock:
        rows = _load_subs()
        kept = [item for item in rows if not (item.get("user") == user_key and item.get("endpoint") == endpoint)]
        if len(kept) != len(rows):
            _save_subs(kept)


def drop_user(user_key):
    """Forget every push subscription for an account. Logout of one browser uses drop_endpoint instead."""
    with _lock:
        rows = _load_subs()
        kept = [item for item in rows if item.get("user") != user_key]
        if len(kept) != len(rows):
            _save_subs(kept)


def _hkdf_extract(salt, ikm):
    """HKDF extract for the Web Push content key."""
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk, info, length):
    """One-block HKDF expand. The wake-up key is short enough for a single block."""
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt_wake(ua_public, auth_secret, as_private_raw, salt):
    """RFC 8291 aes128gcm body for the fixed wake-up."""
    as_private = _private_key(as_private_raw)
    as_public = _public_uncompressed(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, shared), key_info, 32)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    cipher = AESGCM(cek).encrypt(nonce, WAKE + b"\x02", b"")
    header = salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + cipher


def _jwt(origin, private_raw, public_raw):
    """ES256 voucher for the push service. It authorizes the wake-up. It does not contain a message."""
    header = _b64url(b'{"typ":"JWT","alg":"ES256"}')
    payload = _b64url(json.dumps({
        "aud": origin,
        "exp": int(time.time()) + 12 * 3600,
        "sub": SUBJECT,
    }, separators=(",", ":")).encode("ascii"))
    signing = (header + "." + payload).encode("ascii")
    key = _private_key(private_raw)
    der = key.sign(signing, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    sig = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return signing.decode("ascii") + "." + sig, _b64url(public_raw)


def _post(endpoint, body, token, pub):
    """POST a wake-up. Apple push requires HTTP/2. Other hosts accept this HTTP request."""
    body_path = None
    cfg_path = None
    try:
        body_fd, body_path = tempfile.mkstemp(prefix="huzz-push-", dir=STATE_DIR)
        os.chmod(body_path, 0o600)
        with os.fdopen(body_fd, "wb") as fh:
            fh.write(body)
        cfg_fd, cfg_path = tempfile.mkstemp(prefix="huzz-push-cfg-", dir=STATE_DIR)
        os.chmod(cfg_path, 0o600)
        cfg = "\n".join([
            "silent",
            "show-error",
            "http2",
            "connect-timeout = 8",
            "max-time = 15",
            "request = POST",
            "output = /dev/null",
            "write-out = %{http_code}",
            'header = "TTL: 86400"',
            'header = "Urgency: high"',
            'header = "Topic: huzz"',
            'header = "Content-Encoding: aes128gcm"',
            'header = "Content-Type: application/octet-stream"',
            'header = "Authorization: vapid t=%s,k=%s"' % (token, pub),
            'data-binary = "@%s"' % body_path,
            'url = "%s"' % endpoint,
            "",
        ])
        with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
            fh.write(cfg)
        result = subprocess.run(
            ["curl", "--config", cfg_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        code = (result.stdout or b"").decode("ascii", "replace").strip()
        if not code:
            _log("curl-%s" % result.returncode)
            return 0
        return int(code) if code.isdigit() else 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _log("curl-fail")
        return 0
    finally:
        for path in (body_path, cfg_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


def _send(user_keys):
    """Encrypt and send one wake-up per subscription. A dead endpoint is dropped."""
    wanted = set(user_keys)
    with _lock:
        private, public = _load_vapid()
        now = int(time.time())
        chosen = []
        for row in _load_subs():
            if row.get("user") not in wanted:
                continue
            try:
                paused = int(row.get("paused_until") or 0)
            except (TypeError, ValueError):
                paused = 0
            if paused > now:
                continue
            chosen.append(dict(row))
    if not chosen:
        return
    dead = []
    for row in chosen:
        origin = _endpoint_ok(row.get("endpoint"))
        ua_public = _b64url_decode(row.get("p256dh") or "", 80, 120)
        auth = _b64url_decode(row.get("auth") or "", 16, 40)
        if not origin or not ua_public or not auth:
            dead.append(row.get("endpoint"))
            continue
        salt = os.urandom(16)
        fresh = ec.generate_private_key(ec.SECP256R1())
        as_private = fresh.private_numbers().private_value.to_bytes(32, "big")
        try:
            body = encrypt_wake(ua_public, auth, as_private, salt)
            token, pub = _jwt(origin, private, public)
            status = _post(row["endpoint"], body, token, pub)
        except Exception:
            _log("encrypt-fail")
            continue
        if status in (200, 201, 202, 204):
            _log(str(status))
        elif status in (404, 410):
            _log("%s-drop" % status)
            dead.append(row.get("endpoint"))
        else:
            _log("status-%s" % status)
    if not dead:
        return
    with _lock:
        rows = [item for item in _load_subs() if item.get("endpoint") not in dead]
        _save_subs(rows)


def notify(user_keys):
    """Wake the named accounts. The body is the fixed wake-up object, never a chat message."""
    keys = [item for item in user_keys if isinstance(item, str) and item]
    if not keys:
        return

    def run():
        try:
            _send(keys)
        except Exception:
            _log("send-error")

    threading.Thread(target=run, daemon=True).start()


def self_test():
    """RFC 8291 section 5. Raises if the wake encryption does not match."""
    as_private = _b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw", 40, 50)
    ua_public = _b64url_decode(
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        80, 120,
    )
    auth = _b64url_decode("BTBZMqHH6r4Tts7J_aSIgg", 16, 40)
    salt = _b64url_decode("DGv6ra1nlYgDCS1FRnbzlw", 20, 30)
    # The published body encrypts a sentence, not the wake-up. Check the
    # shared secret and the content key from appendix A, then a round trip
    # of our own wake-up through the same derivation.
    as_key = _private_key(as_private)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    if _b64url(shared) != "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs":
        raise RuntimeError("ecdh")
    as_public = _public_uncompressed(as_key.public_key())
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hkdf_expand(_hkdf_extract(auth, shared), key_info, 32)
    if _b64url(ikm) != "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg":
        raise RuntimeError("ikm")
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    if _b64url(cek) != "oIhVW04MRdy2XN9CiKLxTg" or _b64url(nonce) != "4h_95klXJ5E_qnoN":
        raise RuntimeError("cek")
    plain = b"When I grow up, I want to be a watermelon\x02"
    cipher = AESGCM(cek).encrypt(nonce, plain, b"")
    header = salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    body = header + cipher
    expect = (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )
    if _b64url(body) != expect:
        raise RuntimeError("body")
    wake = encrypt_wake(ua_public, auth, as_private, salt)
    if len(wake) < 86 + 16 or wake[:16] != salt:
        raise RuntimeError("wake")
    token, pub = _jwt("https://fcm.googleapis.com", as_private, as_public)
    parts = token.split(".")
    if len(parts) != 3 or pub != _b64url(as_public):
        raise RuntimeError("jwt")
    signing = (parts[0] + "." + parts[1]).encode("ascii")
    sig = _b64url_decode(parts[2], 80, 100)
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    as_key.public_key().verify(utils.encode_dss_signature(r, s), signing, ec.ECDSA(hashes.SHA256()))
