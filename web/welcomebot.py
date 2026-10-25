"""BranchChat Helper: a friendly welcome bot (26.10.3).

What it does, in plain words:
- Every account gets one extra friend, the Helper bot, so nobody starts with an empty chat list.
- The bot sends ONE welcome message, then only speaks when the person writes to it, right after they finish a
  tour step, or (at most once a day, at most three times) a gentle nudge while a tour is unfinished.
  Nothing after "Stop the tour", while the chat is muted, or once the bot has been removed.
- Answers are written ahead of time (welcome-bot.json next to config.env). No AI, just word matching that
  forgives typos.

How it fits the encryption: messages are end-to-end encrypted, so the bot is a real account with its own
"device" key (kept in the data folder, never sent anywhere). People's apps already lock each message for every
device in the chat, which includes the bot's, so the server can read messages sent TO the bot (and only those)
and lock its answers the same way the apps do.

Switches in config.env:
  BC_WELCOME_BOT=1          turn it on (default off)
  BC_WELCOME_BOT_NAME=...   the name people see (default "BranchChat Helper")
  BC_WELCOME_BOT_FILE=...   where the editable words live (default welcome-bot.json beside config.env)
"""

import base64
import copy
import difflib
import json
import os
import random
import re
import secrets
import threading
import time

import bcconfig
import welcomebot_content

USERNAME = "BranchChat-Helper"   # the login-style name; people see the display name instead
KEY = USERNAME.lower()
WRAP_SALT = b"huzz-chat-salt-v1"  # must match web/static/crypto.js
WRAP_INFO = b"huzz-chat-wrap-v1"
NUDGE_GAP = 24 * 3600
NUDGE_MAX = 3

S = None          # the server module, set by init()
_lock = threading.RLock()
_words = {"mtime": None, "data": None}
_state = None
_priv = None
_device_id = ""


def _on(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def enabled():
    return _on(_setting("BC_WELCOME_BOT", "0"))


def display_name():
    return (_setting("BC_WELCOME_BOT_NAME", "") or "BranchChat Helper").strip()[:32]


def _setting(name, default):
    # Read from config.env (the Administrator page edits the same file).
    return bcconfig.get(name, default)


def words_path():
    path = bcconfig.get("BC_WELCOME_BOT_FILE", "").strip()
    if path:
        return path
    base = os.path.dirname(bcconfig.CONFIG_PATH) if bcconfig.CONFIG_PATH else S.STATE_DIR
    return os.path.join(base, "welcome-bot.json")


def words():
    """The editable words. Re-read when the file changes; a broken file keeps the last good copy (and is logged)."""
    path = words_path()
    if not os.path.exists(path):
        try:
            with open(path, "w") as fh:
                json.dump(welcomebot_content.DEFAULTS, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if mtime != _words["mtime"] or _words["data"] is None:
        data = copy.deepcopy(welcomebot_content.DEFAULTS)
        if mtime is not None:
            try:
                with open(path) as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (OSError, ValueError) as exc:
                _log("welcome-bot.json could not be read (%s); using the last good words" % exc.__class__.__name__)
                if _words["data"] is not None:
                    return _words["data"]
        data = _fix_old_text(data)
        # 26.10.5: topics added since the file was copied (like "switch chats") are added too, unless the owner has one.
        have = {t.get("id") for t in (data.get("topics") or []) if isinstance(t, dict)}
        extra = [t for t in welcomebot_content.DEFAULTS.get("topics") or [] if t.get("id") not in have]
        if extra and isinstance(data.get("topics"), list):
            data["topics"] = data["topics"][:-1] + copy.deepcopy(extra) + data["topics"][-1:] if data["topics"] else copy.deepcopy(extra)
        _words["mtime"], _words["data"] = mtime, data
    return _words["data"]


def _fix_old_text(value):
    """26.10.5: swap any old, wrong default wording (see welcomebot_content.OLD_WRONG_TEXT) for the corrected words."""
    fixes = getattr(welcomebot_content, "OLD_WRONG_TEXT", {})
    if isinstance(value, dict):
        return {k: _fix_old_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fix_old_text(v) for v in value]
    if isinstance(value, str):
        return fixes.get(value, value)
    return value


def _with_restart(quick):
    """26.10.5: make sure the tour buttons include "Restart tour", even on servers whose welcome-bot.json predates it."""
    quick = list(quick)
    if not any(isinstance(q, str) and q.strip().lower() == "restart tour" for q in quick):
        quick.insert(max(0, len(quick) - 1), "Restart tour")   # just before "Stop the tour"
    return quick


def _log(text):
    print("[welcome-bot] " + text, flush=True)


def _read_json(path, fallback):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return fallback


# ---------- per-person progress (data/welcome-bot-state.json) ----------

def _state_path():
    return os.path.join(S.STATE_DIR, "welcome-bot-state.json")


def _load():
    global _state
    if _state is None:
        data = _read_json(_state_path(), {})
        _state = data if isinstance(data, dict) else {}
        _state.setdefault("users", {})
    return _state


def _save():
    S.atomic_write(_state_path(), json.dumps(_state))


def _person(key):
    users = _load()["users"]
    row = users.get(key)
    if not isinstance(row, dict):
        row = {"done": [], "skipped": []}
        users[key] = row
    return row


# ---------- the bot's account and device key ----------

def is_bot(key_or_name):
    return isinstance(key_or_name, str) and key_or_name.strip().lower() == KEY


def init(server_module):
    """Called once at server start. Does nothing visible while the bot is switched off."""
    global S
    S = server_module
    if enabled():
        start()


def start():
    with _lock:
        _ensure_account()
        _ensure_device()
        _sync_display_name()
        _ensure_avatar()
        words()
        _add_everyone()
        _leave_groups_and_lobby()
    if not getattr(start, "_ticker", None):
        start._ticker = threading.Thread(target=_ticker, daemon=True, name="welcome-bot-nudges")
        start._ticker.start()


def _ensure_account():
    import accounts
    if accounts.find_user(KEY):
        return
    with accounts._lock:
        data = accounts._read()
        if any(u.get("username_key") == KEY for u in data["users"]):
            return
        data["users"].append({
            "id": secrets.token_hex(16), "username": USERNAME, "username_key": KEY, "email": "", "role": "member",
            # A random password nobody ever sees: the account exists so friends can message it, but no one can sign in.
            "password": accounts.hash_password(secrets.token_urlsafe(48)),
            "session_version": 1, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "bot": True,
        })
        accounts._write(data)
    _log("bot account created")


def _ensure_device():
    global _priv, _device_id
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    path = os.path.join(S.STATE_DIR, "welcome-bot-key.json")
    saved = _read_json(path, {})
    if saved.get("pem") and saved.get("device_id"):
        _priv = serialization.load_pem_private_key(saved["pem"].encode(), password=None)
        _device_id = saved["device_id"]
    else:
        _priv = ec.generate_private_key(ec.SECP256R1())
        _device_id = secrets.token_hex(16)
        pem = _priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
        S.atomic_write(path, json.dumps({"pem": pem, "device_id": _device_id}))
        os.chmod(path, 0o600)
    pub = base64.b64encode(_priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    with S._store_lock:
        rows = S.load_devices()
        if not any(r.get("device_id") == _device_id and r.get("user") == KEY for r in rows):
            rows = [r for r in rows if r.get("user") != KEY]
            rows.append({"user": KEY, "name": USERNAME, "device_id": _device_id, "public_key": pub, "updated": int(time.time())})
            S.save_devices(rows)


def _ensure_avatar():
    """Give the bot the BranchChat tree icon as its picture (only if it has none yet)."""
    try:
        path = S.avatar_path(KEY)
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "welcome-bot-avatar.jpg")
        if path and not os.path.exists(path) and os.path.exists(src):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(src, "rb") as fh:
                data = fh.read()
            with open(path, "wb") as fh:
                fh.write(data)
    except OSError:
        pass


def _sync_display_name():
    if S.load_display_names().get(KEY) != display_name():
        S.set_display_name(KEY, display_name())


def _real_accounts():
    import accounts
    with accounts._lock:
        users = accounts._read()["users"]
    return [u for u in users if u.get("password") and not u.get("disabled") and u.get("username_key") != KEY]


def _add_everyone():
    """Existing accounts get the bot as a friend once. Their welcome waits until they next open BranchChat."""
    changed = False
    for u in _real_accounts():
        row = _person(u["username_key"])
        if not row.get("added"):
            S.make_friends(u["username_key"], KEY)
            row["added"] = int(time.time())
            row["existing"] = True
            changed = True
    if changed:
        _save()


def _leave_groups_and_lobby():
    """The bot is one-to-one only: take it out of any group and the Lobby it somehow ended up in."""
    with S._store_lock:
        for g in list(S.groups_of(KEY)):
            try:
                S.group_leave(g, KEY)   # saves the group and posts the usual "left" notice
                _log("removed from a group")
            except Exception as exc:
                _log("group cleanup error %s" % exc.__class__.__name__)
        lobby = S.load_lobby()
        if KEY in lobby["members"]:
            lobby["members"].pop(KEY, None)
            S.save_lobby(lobby)
            _log("removed from the Lobby")


# ---------- encryption (same recipe as web/static/crypto.js) ----------

def _aes_from_shared(secret):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=WRAP_SALT, info=WRAP_INFO).derive(secret)


def _wrap_for(public_key_b64, key_bytes):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    peer = serialization.load_der_public_key(base64.b64decode(public_key_b64))
    eph = ec.generate_private_key(ec.SECP256R1())
    aes = AESGCM(_aes_from_shared(eph.exchange(ec.ECDH(), peer)))
    iv = os.urandom(12)
    ct = aes.encrypt(iv, key_bytes, None)
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"eph_pub": base64.b64encode(eph_pub).decode(), "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def _open(row):
    """Read a message someone sent to the bot. Returns the text or ''."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    for wrap in row.get("wraps") or []:
        if wrap.get("device_id") != _device_id:
            continue
        try:
            eph = serialization.load_der_public_key(base64.b64decode(wrap["eph_pub"]))
            key = AESGCM(_aes_from_shared(_priv.exchange(ec.ECDH(), eph))).decrypt(
                base64.b64decode(wrap["iv"]), base64.b64decode(wrap["ct"]), None)
            plain = AESGCM(key).decrypt(base64.b64decode(row["iv"]), base64.b64decode(row["ct"]), None)
            payload = json.loads(plain.decode("utf-8"))
            text = payload.get("text") if isinstance(payload, dict) else ""
            return text if isinstance(text, str) else ""
        except Exception:
            return ""
    return ""


def _send(to_key, text, quick=None):
    """Store one encrypted message from the bot to one person (locked for their devices and the bot's)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    payload = {"text": text, "file": None, "bot": True}
    if quick:
        payload["quick"] = [q for q in quick if isinstance(q, str)][:6]
    key_bytes = os.urandom(32)
    iv = os.urandom(12)
    ct = AESGCM(key_bytes).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    with S._store_lock:
        devices = S.load_devices()
        targets = [d for d in devices if d.get("user") in (to_key, KEY) and d.get("public_key")]
        if not any(d.get("user") == to_key for d in targets):
            return False   # they have not opened BranchChat on any device yet; try again on their next visit
        wraps = []
        for d in targets[:16]:
            w = _wrap_for(d["public_key"], key_bytes)
            w["device_id"] = d["device_id"]
            w["user"] = d["user"]
            wraps.append(w)
        pair = sorted([to_key, KEY])
        row = {"box": "dm", "a": pair[0], "b": pair[1], "epoch": 0,
               "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode(),
               "wraps": wraps, "epoch_iv": "", "epoch_ct": "", "blob": None}
        row["seq"] = (S._messages[-1]["seq"] + 1) if S._messages else 1
        row["id"] = secrets.token_hex(16)
        row["sender"] = KEY
        row["sender_name"] = USERNAME
        row["ts"] = int(time.time())
        S.append_message(row)
    return True


# ---------- hooks the server calls ----------

def _active_for(key):
    return enabled() and key and not is_bot(key)


def ensure(user):
    """Every sync: make sure this person has the bot as a friend (once) and got the welcome (once)."""
    key = user.get("username_key")
    if not _active_for(key) or _priv is None:
        return
    with _lock:
        row = _person(key)
        if row.get("removed"):
            return
        _rewrap(key)
        if not row.get("added"):
            S.make_friends(key, KEY)
            row["added"] = int(time.time())
            _save()
        if not row.get("welcomed"):
            w = words()
            name = _name_of(key, user)
            text = _pick(w, row, "existing_welcome" if row.get("existing") else "welcome", name)
            quick = w.get("welcome_quick") or []
            if _send(key, text, quick):
                row["welcomed"] = row["last_bot"] = int(time.time())
                row["mode"] = "offer"
                row["quick"] = quick
                _save()


def _rewrap(key):
    """Share the key of every message in this person's Helper chat with any of their devices that lacks it.

    Why: the apps lock each message only for the devices that existed when it was sent. Between people, another
    device of yours passes the key on later; the bot has no app to do that, so a phone signed in after the welcome
    (or a browser whose key was replaced) showed "Waiting for this device to get the key" for good. The bot can open
    its own copy of each key, so it hands it to the new device here (once per device set, so this stays cheap)."""
    if _priv is None:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with S._store_lock:
        mine = {d["device_id"]: d for d in S.load_devices() if d.get("user") == key and d.get("public_key") and d.get("device_id")}
        sig = ",".join(sorted(mine))
        row = _person(key)
        if not mine or row.get("wrapped_for") == sig:
            return
        pair = sorted([key, KEY])
        changed = False
        for msg in S._messages:
            if msg.get("box") != "dm" or [msg.get("a"), msg.get("b")] != pair or msg.get("deleted"):
                continue
            have = {w.get("device_id") for w in msg.get("wraps") or []}
            missing = [d for d in mine if d not in have]
            if not missing:
                continue
            own = next((w for w in msg.get("wraps") or [] if w.get("device_id") == _device_id), None)
            if not own:
                continue
            try:
                eph = serialization.load_der_public_key(base64.b64decode(own["eph_pub"]))
                msg_key = AESGCM(_aes_from_shared(_priv.exchange(ec.ECDH(), eph))).decrypt(
                    base64.b64decode(own["iv"]), base64.b64decode(own["ct"]), None)
            except Exception:
                continue
            for dev in missing:
                w = _wrap_for(mine[dev]["public_key"], msg_key)
                w["device_id"] = dev
                w["user"] = key
                msg.setdefault("wraps", []).append(w)
            # Re-send the row to devices that already have the chat open (no "Edited" mark: only edited_ts shows that).
            msg["edited_seq"] = S.next_edited_seq()
            changed = True
        if changed:
            S.rewrite_messages_now()   # 26.10.8: ordered with queued appends; indexes refreshed
        row["wrapped_for"] = sig
    _save()


def is_friend_of_bot(key):
    return _active_for(key) and not _person(key).get("removed")


def removed(key, other_key):
    """Someone removed or blocked the bot: never add it back or message them again."""
    if not is_bot(other_key) or not key:
        return
    with _lock:
        row = _person(key)
        row["removed"] = int(time.time())
        _save()


def step(key, step_id):
    """The server saw the person do something (sent a message, reacted, joined the Lobby, made a group, ...)."""
    if not _active_for(key):
        return
    threading.Thread(target=_step_safe, args=(key, step_id), daemon=True).start()


def _step_safe(key, step_id):
    try:
        with _lock:
            row = _person(key)
            if step_id in row["done"]:
                return
            row["done"].append(step_id)
            _save()
            # Only speak if this is the step the tour is waiting on, so people are not flooded.
            if row.get("mode") == "tour" and row.get("current") == step_id and not _quiet(key, row):
                w = words()
                st = _step_by_id(w, step_id)
                cheer = _choice(st.get("cheer") or ["Nice work!"], row, "cheer:" + step_id) if st else "Nice work!"
                time.sleep(0.8)
                _advance(key, row, w, _name_of(key), lead=cheer)
    except Exception as exc:
        _log("step error %s" % exc.__class__.__name__)


def message(user, row, view=None):
    """A message was just stored. If it was sent to the bot, answer it (after a short, natural pause)."""
    key = user.get("username_key")
    if not _active_for(key) or row.get("box") != "dm" or _priv is None:
        return
    if KEY not in (row.get("a"), row.get("b")) or row.get("sender") == KEY:
        return
    note_view(key, view)   # 26.10.5: phone or computer layout, sent by the app
    threading.Thread(target=_reply_safe, args=(user, row), daemon=True).start()


def _reply_safe(user, row):
    try:
        time.sleep(0.9)
        text = _open(row)
        with _lock:
            _reply(user["username_key"], text, user)
    except Exception as exc:
        _log("reply error %s" % exc.__class__.__name__)


# ---------- the conversation ----------

def _name_of(key, user=None):
    dn = S.load_display_names().get(key)
    return dn or (user or {}).get("username") or S.name_of(key) or "friend"


def _choice(items, row, slot):
    """Pick a reply, avoiding the one used last time for the same topic."""
    items = [i for i in items if isinstance(i, (str, dict))] or ["🙂"]   # 26.10.5: a dict is a phone/computer pair
    last = row.setdefault("last", {}).get(slot)
    pool = [i for i in range(len(items)) if i != last] or [0]
    idx = random.choice(pool)
    row["last"][slot] = idx
    return items[idx]


def _pick(w, row, slot, name):
    return _fill(_choice(w.get(slot) or [""], row, slot), name)


# 26.10.5: which layout the person is looking at, so directions match their screen. "phone" = the app's narrow layout
# (window 800px wide or less: one screen at a time, back arrow, menu in the chat's top bar); "computer" = the wide
# layout (chat list on the left, menu at the top right of the window). The app sends this with each message to the bot.
VIEWS = ("phone", "computer")
_view_now = {"v": ""}


def _for_view(text, view=None):
    """Pick the phone or computer version of a direction. Unknown layout: give both, clearly labelled."""
    if not isinstance(text, dict):
        return text if isinstance(text, str) else ""
    view = view if view is not None else _view_now["v"]
    if view in VIEWS and isinstance(text.get(view), str):
        return text[view]
    phone, comp = text.get("phone"), text.get("computer")
    if isinstance(phone, str) and isinstance(comp, str):
        return "On a phone: " + phone + "\n\nOn a computer: " + comp
    return phone if isinstance(phone, str) else (comp if isinstance(comp, str) else "")


def note_view(key, view):
    """Remember the layout the app last reported for this person (ignored unless it is one we know)."""
    if view in VIEWS:
        with _lock:
            row = _person(key)
            if row.get("view") != view:
                row["view"] = view
                _save()


def _fill(text, name):
    text = _for_view(text)
    email = bcconfig.CONTACT_EMAIL
    w = words()
    line = (w.get("contact_line_with_email") or "").replace("{email}", email) if email else (w.get("contact_line_without_email") or "")
    return text.replace("{contact_line}", line).replace("{name}", name)


def _steps(w):
    return [s for s in (w.get("tour_steps") or []) if isinstance(s, dict) and s.get("id")]


def _step_by_id(w, sid):
    for s in _steps(w):
        if s["id"] == sid:
            return s
    return None


def _remaining(w, row):
    return [s for s in _steps(w) if s["id"] not in row["done"] and s["id"] not in row["skipped"]]


def _quiet(key, row):
    """Muted chat, stopped tour or removed bot: no extra messages."""
    if row.get("removed") or row.get("mode") == "stopped":
        return True
    try:
        return S.chat_muted(key, "dm:" + KEY, S.load_mutes())
    except Exception:
        return False


def _advance(key, row, w, name, lead=""):
    left = _remaining(w, row)
    parts = [lead] if lead else []
    if not left:
        row["mode"] = "finished"
        row["current"] = ""
        parts.append(_pick(w, row, "tour_finished", name))
        quick = ["Restart tour"]   # 26.10.5: an easy way to see it all again
    else:
        st = left[0]
        row["mode"] = "tour"
        row["current"] = st["id"]
        if lead:
            parts.append(_choice(w.get("tour_next_lead") or [""], row, "lead"))
        parts.append(_fill(st.get("ask") or "", name))
        quick = _with_restart(w.get("tour_quick") or [])
    row["quick"] = quick
    row["last_bot"] = int(time.time())
    _save()
    _send(key, " ".join(p for p in parts if p), quick)


NORM_RE = re.compile(r"[^a-z0-9' ]+")


def _norm(text):
    text = (text or "").lower().replace("’", "'")
    text = NORM_RE.sub(" ", text)
    return " ".join(text.split())


def _word_hit(word, tokens):
    if word in tokens or word.replace("'", "") in tokens:
        return True
    if len(word) < 4:
        return False
    return any(len(t) >= 3 and difflib.SequenceMatcher(None, word, t).ratio() >= 0.8 for t in tokens)


def _score(phrase, text, tokens):
    """How well a phrase matches: exact phrase beats all-words-fuzzy; longer phrases beat short ones."""
    p = _norm(phrase)
    if not p:
        return 0
    if (" " + p + " ") in (" " + text + " ") or (" " + p.replace("'", "") + " ") in (" " + text.replace("'", "") + " "):
        return 10 + len(p)
    words_ = p.split()
    if all(_word_hit(x, tokens) for x in words_):
        return 5 + len(p)
    return 0


def _best(groups, text, tokens):
    best, best_score = None, 0
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        for phrase in g.get("match") or []:
            sc = _score(phrase, text, tokens)
            if sc > best_score:
                best, best_score = g, sc
    return best, best_score


COMMANDS = {
    "stop": ["stop the tour", "stop tour", "end tour", "quit tour", "no more tour", "stop tips"],
    "left": ["what's left", "whats left", "what is left", "remaining", "what's next", "whats next", "steps left"],
    # 26.10.5: "Restart tour" starts again from step one, forgetting what was done or skipped (plain "tour" only resumes).
    "restart": ["restart tour", "restart the tour", "start the tour again", "start over", "tour again", "restart", "reset tour"],
    "tour": ["start the tour", "take the tour", "tour", "start tour", "show me around", "walkthrough"],
    "help": ["help", "what can you do", "menu", "options", "what can you help with"],
    "how": ["show me how", "how do i", "how", "not yet", "show me", "i haven't", "i havent"],
    "done": ["done", "did it", "i did", "finished", "already did", "yes i have", "i have", "tried it", "already"],
    "skip": ["skip", "next", "skip it", "pass", "later"],
    "notnow": ["not now", "maybe later", "no thanks"],
}


def _command(text, tokens):
    best, best_score = None, 0
    for cmd, phrases in COMMANDS.items():
        for p in phrases:
            sc = _score(p, text, tokens)
            # Commands must be (nearly) the whole message, so "how do I make a group?" is a question, not "Show me how".
            # Only a few clear multi-word ones ("stop the tour", "what's left") also count inside a longer sentence.
            loose = cmd in ("stop", "left", "tour", "restart") and len(p.split()) >= 2 and sc >= 10
            if sc and sc > best_score and (loose or len(tokens) <= len(p.split()) + 1):
                best, best_score = cmd, sc
    return best


def _reply(key, raw, user):
    w = words()
    row = _person(key)
    if row.get("removed"):
        return
    name = _name_of(key, user)
    _view_now["v"] = row.get("view") or ""   # 26.10.5: answer for the screen they are on
    row["last_user"] = int(time.time())
    text = _norm(raw)
    tokens = text.split()
    # A bare number picks one of the buttons the bot offered last (for apps that show them as a list).
    if text.isdigit() and row.get("quick"):
        n = int(text)
        if 1 <= n <= len(row["quick"]):
            text = _norm(row["quick"][n - 1])
            tokens = text.split()
    mode = row.get("mode") or ""
    cmd = _command(text, tokens)
    # Writing to the bot counts as "send your first message".
    if "first_message" not in row["done"]:
        row["done"].append("first_message")
        if mode == "tour" and row.get("current") == "first_message" and cmd is None:
            st = _step_by_id(w, "first_message") or {}
            _advance(key, row, w, name, lead=_fill(_choice(st.get("cheer") or ["Nice!"], row, "cheer:first_message"), name))
            return
    if mode == "offer" and cmd is None:
        sm, _ = _best(w.get("smalltalk"), text, tokens)
        if sm and sm.get("id") == "yes":
            cmd = "tour"
        elif sm and sm.get("id") == "no":
            cmd = "notnow"
    if mode == "tour" and cmd is None:
        sm, sc = _best(w.get("smalltalk"), text, tokens)
        if sm and sm.get("id") == "yes" and len(tokens) <= 3:
            cmd = "done"
        elif sm and sm.get("id") == "no" and len(tokens) <= 3:
            cmd = "how"

    if cmd == "stop":
        row["mode"] = "stopped"
        row["quick"] = []
        _save()
        _send(key, _pick(w, row, "tour_stopped", name))
        return
    if cmd == "notnow":
        row["mode"] = "idle"
        row["quick"] = []
        _save()
        _send(key, _pick(w, row, "tour_not_now", name))
        return
    if cmd == "restart":
        row["done"], row["skipped"] = ["first_message"], []   # they just wrote to the bot, so that step stays done
        row["nudges"] = 0
        row["mode"] = "tour"
        _advance(key, row, w, name, lead=_pick(w, row, "tour_restarted", name))
        return
    if cmd == "tour":
        row["skipped"] = []
        row["nudges"] = 0
        _advance(key, row, w, name)
        return
    if cmd == "left":
        left = _remaining(w, row)
        if not left:
            _send(key, _pick(w, row, "whats_left_none", name))
            return
        lines = [_pick(w, row, "whats_left_intro", name)] + ["• " + _fill(s.get("ask") or s["id"], name) for s in left]
        row["quick"] = ["Take the tour", "Restart tour", "Stop the tour"]
        _save()
        _send(key, "\n".join(lines), row["quick"])
        return
    if cmd == "help":
        row["quick"] = w.get("fallback_quick") or []
        _save()
        _send(key, _fill(w["fallback"][0], name), row["quick"])
        return
    if mode == "tour" and cmd in ("how", "done", "skip"):
        st = _step_by_id(w, row.get("current") or "")
        if st and cmd == "how":
            row["quick"] = ["Done", "Skip", "Restart tour", "Stop the tour"]
            row["last_bot"] = int(time.time())
            _save()
            _send(key, _fill(st.get("how") or "", name), row["quick"])
            return
        if st and cmd == "done":
            if st["id"] not in row["done"]:
                row["done"].append(st["id"])
            _advance(key, row, w, name, lead=_fill(_choice(st.get("cheer") or ["Nice!"], row, "cheer:" + st["id"]), name))
            return
        if st and cmd == "skip":
            row["skipped"].append(st["id"])
            _advance(key, row, w, name, lead=_pick(w, row, "tour_skip", name))
            return

    # Questions about BranchChat beat small talk ("hi, how do I make a group?" answers the group question).
    topic, tsc = _best(w.get("topics"), text, tokens)
    small, ssc = _best(w.get("smalltalk"), text, tokens)
    if small and small.get("id") == "serious":
        tsc = 0   # a cry for help always gets the kind answer
    if topic and tsc >= ssc and tsc > 0:
        reply = _fill(_choice(topic.get("replies") or [], row, "t:" + topic["id"]), name)
        _send(key, reply + _tieback(w, row, name, chance=0.3))
        _save()
        return
    if small:
        reply = _fill(_choice(small.get("replies") or [], row, "s:" + small["id"]), name)
        tie = "" if small.get("no_tieback") else _tieback(w, row, name, chance=0.35)
        _send(key, reply + tie, row.get("quick") if tie else None)
        _save()
        return
    row["quick"] = w.get("fallback_quick") or []
    _save()
    _send(key, _pick(w, row, "fallback", name), row["quick"])


def _tieback(w, row, name, chance):
    """Sometimes (not every time) point back to the next tour step."""
    if row.get("mode") in ("stopped", "finished") or random.random() > chance:
        return ""
    left = _remaining(w, row)
    if not left:
        return ""
    st = left[0]
    row["mode"] = "tour"
    row["current"] = st["id"]
    row["quick"] = _with_restart(w.get("tour_quick") or [])
    lead = _choice(w.get("tieback") or ["By the way,"], row, "tie")
    ask = _fill(st.get("ask") or "", name)
    if lead.endswith(",") and ask[:1].isupper() and not ask.startswith("I "):
        ask = ask[:1].lower() + ask[1:]   # "By the way, have you ..." reads better than "By the way, Have you ..."
    return " " + lead + " " + ask


def _ticker():
    """Once in a while: one gentle nudge a day, at most three, for people who went quiet in the middle of the tour."""
    while True:
        time.sleep(600)
        if not enabled():
            continue
        try:
            now = int(time.time())
            with _lock:
                w = words()
                for key, row in list(_load()["users"].items()):
                    if row.get("mode") != "tour" or _quiet(key, row) or int(row.get("nudges") or 0) >= NUDGE_MAX:
                        continue
                    last = max(int(row.get("last_bot") or 0), int(row.get("last_user") or 0), int(row.get("last_nudge") or 0))
                    if now - last < NUDGE_GAP:
                        continue
                    row["nudges"] = int(row.get("nudges") or 0) + 1
                    row["last_nudge"] = now
                    st = _step_by_id(w, row.get("current") or "")
                    if not st:
                        continue
                    name = _name_of(key)
                    _view_now["v"] = row.get("view") or ""   # 26.10.5
                    _save()
                    _send(key, _pick(w, row, "nudge", name) + " " + _fill(st.get("ask") or "", name), _with_restart(w.get("tour_quick") or []))
        except Exception as exc:
            _log("nudge error %s" % exc.__class__.__name__)
