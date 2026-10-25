"""26.10.4: "What's new" email for brand-new accounts.

When someone registers (accepts an invite, or signs up on a public server), the server emails them the single
most recent feature-update announcement, so they start out knowing what's new. Existing members are never mailed
by this. It is the app's own automatic email; there is no "send to everyone" button here.

Switches (config.env):
  BC_NEWUSER_FEATURE_EMAIL=1        turn this on (default off)
  BC_NEWUSER_FEATURE_EMAIL_DELAY=120 seconds to wait after sign-up before sending (default 120)
  BC_FEATURE_EMAILS=0               master off for every feature-news email (wins over the line above)
  BC_FEATURE_EMAILS_FILE=/path.json use a different list of announcements (default web/feature_emails.json)

The announcements live in feature_emails.json. To add a new one when an update ships, append an entry with a
newer "date"; "the latest" is simply the entry with the newest date, so nothing else needs changing.
"""
import hashlib
import hmac
import json
import os
import threading
import time
from urllib.parse import quote

import accounts
import bcconfig
import mailtemplates

HERE = os.path.dirname(os.path.abspath(__file__))


def _on(name, default=""):
    return bcconfig.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return max(0, int(bcconfig.get(name, str(default)).strip() or default))
    except ValueError:
        return default


MASTER_OFF = bcconfig.get("BC_FEATURE_EMAILS", "").strip().lower() in ("0", "false", "no", "off")
ENABLED = _on("BC_NEWUSER_FEATURE_EMAIL") and not MASTER_OFF
DELAY = _int("BC_NEWUSER_FEATURE_EMAIL_DELAY", 120)
STORE_PATH = bcconfig.get("BC_FEATURE_EMAILS_FILE", "").strip() or os.path.join(HERE, "feature_emails.json")
STATE_PATH = os.path.join(bcconfig.STATE_DIR, "feature-email-state.json")
RETRIES = 3            # tries per email before giving up
RETRY_GAP = 600        # seconds between tries
TICK = 30              # how often the little worker looks for emails that are due

# Domains that are never real inboxes (test accounts, placeholders), so we never try to mail them.
_FAKE_DOMAINS = ("invalid", "local", "localhost", "test", "example")

_lock = threading.Lock()
_worker = None


# ---------- the list of announcements ----------

def entries():
    """Every announcement in the store, oldest first. A broken or missing file is just an empty list."""
    try:
        with open(STORE_PATH, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return []
    good = [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("subject") and r.get("paragraphs")]
    # Stable sort by date: same-date entries keep file order, so the one added last counts as newer.
    return sorted(good, key=lambda r: str(r.get("date") or ""))


def latest():
    """The newest announcement, or None when the store is empty."""
    rows = entries()
    return rows[-1] if rows else None


def find(entry_id):
    for row in entries():
        if row.get("id") == entry_id:
            return row
    return None


# ---------- unsubscribe link (shared with the mass announcement email) ----------

def off_token(key, action="off"):
    """Signed token for this account's unsubscribe ("off") or resubscribe ("on") link, so nobody can change it for
    someone else. HMAC with this server's own secret, so a link from one server never works on another. The token is
    stateless: using it again just repeats the same, harmless change."""
    return hmac.new(accounts._session_key(), ("feature-news-%s:%s" % (action, key)).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def off_url(key, action="off"):
    return "%s/feature-news/%s?u=%s&t=%s" % (accounts.PUBLIC_URL, action, quote(key), off_token(key, action))


def token_ok(key, token, action="off"):
    return bool(key) and isinstance(token, str) and hmac.compare_digest(off_token(key, action), token)


def wants_news(user):
    """26.10.11: the account's "Feature update emails" switch (stored as feature_news; default on)."""
    return bool(user) and user.get("feature_news") is not False


def set_news(key, on):
    """Turn feature update emails on or off for one account. Returns True when the account exists.
    Sign-in codes, invites and other account emails never look at this."""
    with accounts._lock:
        data = accounts._read()
        for user in data["users"]:
            if user.get("username_key") == key:
                user["feature_news"] = bool(on)
                accounts._write(data)
                return True
    return False


def turn_off(key):
    return set_news(key, False)


# ---------- building one email ----------

def render(entry, email, key):
    """(subject, plain text, html) for one announcement, in the shared light BranchChat template."""
    subject = entry["subject"]
    paragraphs = [str(p) for p in entry.get("paragraphs") or []]
    link = off_url(key) if key else ""
    # "{site}" in a button link becomes this server's own address, so one entry works on every server.
    button_url = (entry.get("button_url") or "").replace("{site}", accounts.PUBLIC_URL)
    entry = dict(entry, button_url=button_url)
    html = mailtemplates.announcement(
        email, subject, entry.get("headline") or subject, paragraphs,
        button_label=entry.get("button_label") or "", button_url=entry.get("button_url") or "",
        off_url=link,
        why="You got this because you just joined %s with %s. It's a one-time welcome note about what's new."
            % (mailtemplates.server_name(), email))
    text = "\n\n".join(paragraphs)
    if entry.get("button_url"):
        text += "\n\n%s: %s" % (entry.get("button_label") or "Open", entry["button_url"])
    text += "\n\n--\nYou got this because you just joined %s with %s." % (mailtemplates.server_name(), email)
    if link:
        text += "\nUnsubscribe from feature update emails: %s" % link
    text += "\nSent by BranchChat\u2122 \u00b7 private chat for families and friends\n"
    return subject, text, html


def _with_unsubscribe_headers(message, key):
    """Add List-Unsubscribe headers (Gmail/Apple show an "Unsubscribe" button) right after the first header line."""
    if not key:
        return message
    head, sep, rest = message.partition("\n")
    extra = ("List-Unsubscribe: <%s>\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\n" % off_url(key))
    return head + sep + extra + rest


def send_entry(entry, email, key):
    """Build and hand one announcement to the server's normal mail path. True when it was handed off."""
    subject, text, html = render(entry, email, key)
    message = accounts.compose_mail(email, accounts.MAIL_FROM, subject, text, html)
    return accounts.deliver_mail(_with_unsubscribe_headers(message, key))


# ---------- the small state file (who got what, and what is waiting) ----------

def _read_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("sent", {})
    data.setdefault("queue", [])
    return data


def _write_state(data):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def _real_email(email):
    if not isinstance(email, str) or not accounts.EMAIL_RE.match(email) or any(c in email for c in "\r\n<>,"):
        return False
    domain = email.rsplit("@", 1)[1].lower()
    labels = domain.split(".")
    return not any(label in _FAKE_DOMAINS for label in labels)


def _created_ts(user):
    try:
        return time.mktime(time.strptime(user.get("created") or "", "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0


# ---------- the hook called by sign-up ----------

def new_user(user):
    """Called ONLY from the two places that create a brand-new account (invite accept and public sign-up).
    Queues the latest announcement for that person. Never raises; sign-up must not fail because of email."""
    try:
        return _queue_for(user)
    except Exception as exc:
        print("[feature-email] not queued: %s" % exc.__class__.__name__, flush=True)
        return ""


def _queue_for(user):
    if not ENABLED or not user:
        return "off"
    key = user.get("username_key") or ""
    if not key or user.get("bot") or user.get("feature_news") is False:
        return "skip"
    if not _real_email(user.get("email")):
        return "no-email"
    entry = latest()
    if not entry:
        return "empty"
    with _lock:
        state = _read_state()
        # Safety net: an account made before this was switched on is an existing member, never "new".
        enabled_at = float(state.get("enabled_at") or time.time())
        if 0 < _created_ts(user) < enabled_at - 60:
            return "existing"
        # Dedupe: one copy of each announcement per person, ever (sent or already waiting).
        if entry["id"] in state["sent"].get(key, {}):
            return "dupe"
        if any(q.get("key") == key and q.get("id") == entry["id"] for q in state["queue"]):
            return "dupe"
        state["queue"].append({"key": key, "id": entry["id"], "due": int(time.time()) + DELAY, "tries": 0})
        _write_state(state)
    print("[feature-email] queued latest announcement for a new account", flush=True)
    return "queued"


def run_due(now=None):
    """Send every queued email whose time has come. Looks the account up again first, so a deleted account,
    a changed address, or "turn off feature news" since sign-up is respected."""
    now = now or time.time()
    with _lock:
        state = _read_state()
        due = [q for q in state["queue"] if q.get("due", 0) <= now]
    for item in due:
        user = accounts.find_user(item["key"])
        entry = find(item["id"])
        ok, drop = False, False
        if not user or not entry or user.get("feature_news") is False or MASTER_OFF \
                or not _real_email(user.get("email")):
            drop = True
        else:
            ok = send_entry(entry, user["email"], item["key"])
        with _lock:
            state = _read_state()
            row = next((q for q in state["queue"] if q.get("key") == item["key"] and q.get("id") == item["id"]), None)
            if row is None:
                continue
            if ok or drop:
                state["queue"].remove(row)
                if ok:
                    state["sent"].setdefault(item["key"], {})[item["id"]] = int(time.time())
            else:
                row["tries"] = int(row.get("tries") or 0) + 1
                if row["tries"] >= RETRIES:
                    state["queue"].remove(row)
                    state["sent"].setdefault(item["key"], {})[item["id"]] = -int(time.time())  # gave up
                else:
                    row["due"] = int(time.time()) + RETRY_GAP
            _write_state(state)
        print("[feature-email] %s" % ("sent" if ok else ("dropped" if drop else "send failed, will retry")), flush=True)


def _loop():
    while True:
        time.sleep(TICK)
        try:
            run_due()
        except Exception as exc:
            print("[feature-email] worker: %s" % exc.__class__.__name__, flush=True)


def init():
    """Start the worker and note when the feature was first switched on. Does nothing while it is off."""
    global _worker
    if not ENABLED:
        return
    with _lock:
        state = _read_state()
        if not state.get("enabled_at"):
            state["enabled_at"] = int(time.time())
            _write_state(state)
    if _worker is None:
        _worker = threading.Thread(target=_loop, daemon=True, name="feature-email")
        _worker.start()


# ---------- the "Turn off feature news" page ----------

_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feature news - BranchChat</title></head>
<body style="margin:0;background:#e7ece4;font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:#26362c;">
<div style="max-width:480px;margin:48px auto;background:#fbfcf9;border:1px solid #d3ddd0;border-radius:16px;padding:28px 32px;">
<p style="margin:0 0 14px 0;font-size:22px;"><b>Branch</b><span style="font-weight:300;color:#2e7d48;">Chat</span><sup style="font-size:9px;">TM</sup></p>
%s</div></body></html>"""


_BTN = ("<form method=\"post\"><button type=\"submit\" style=\"background:#2e7d48;color:#fff;border:0;"
        "border-radius:10px;padding:12px 20px;font-size:16px;cursor:pointer;\">%s</button></form>")


def handle(h, method, path, query):
    """Serve /feature-news/off and (26.10.11) /feature-news/on. GET shows a confirm button (so mail scanners that open
    links can't change anything); POST (the button, or a mail app's RFC 8058 one-click unsubscribe, which POSTs
    List-Unsubscribe=One-Click to the List-Unsubscribe address) makes the change. No sign-in needed: the signed token
    is the proof. Returns True when handled."""
    if path not in ("/feature-news/off", "/feature-news/on") or method not in ("GET", "POST"):
        return False
    action = path.rsplit("/", 1)[1]
    key = ((query.get("u") or [""])[0] or "").strip().lower()
    token = (query.get("t") or [""])[0] or ""
    import html as _html
    if not token_ok(key, token, action) or not accounts.find_user(key):
        body = "<p>That link isn&rsquo;t valid. You can also change this in BranchChat: open the menu and use <b>Feature update emails</b>.</p>"
        status = 400
    elif method == "GET":
        status = 200
        body = ("<p>Stop getting &ldquo;What&rsquo;s new&rdquo; feature update emails? Sign-in codes, invites and other account emails still arrive.</p>" + _BTN % "Unsubscribe"
                if action == "off" else "<p>Get &ldquo;What&rsquo;s new&rdquo; feature update emails again?</p>" + _BTN % "Resubscribe")
    else:
        status = 200
        h.close_connection = True  # the tiny form body is not read, so don't reuse this connection
        set_news(key, action == "on")
        if action == "off":
            body = ("<p>Done. You&rsquo;re unsubscribed from feature update emails. Sign-in codes, invites and other account emails still arrive.</p>"
                    "<p>Changed your mind? <a href=\"%s\" style=\"color:#256b3c;\">Resubscribe</a></p>" % _html.escape(off_url(key, "on")))
        else:
            body = ("<p>Done. You&rsquo;ll get feature update emails again.</p>"
                    "<p><a href=\"%s\" style=\"color:#256b3c;\">Unsubscribe</a></p>" % _html.escape(off_url(key, "off")))
    h.send_bytes(status, (_PAGE % body).encode("utf-8"), "text/html; charset=utf-8",
                 [("Cache-Control", "no-store"),
                  ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")])
    return True


# ---------- 26.10.11: sending a feature email to every member (run by hand, only when Luke approves) ----------

def recipients(users=None):
    """(key, email) for every account that should get a feature update email: real people with a real address
    who haven't turned feature update emails off. Bots, disabled accounts and opted-out accounts are skipped."""
    users = accounts._read()["users"] if users is None else users
    out = []
    for u in users:
        if u.get("bot") or u.get("disabled") or not wants_news(u) or not _real_email(u.get("email")):
            continue
        out.append((u.get("username_key") or "", u["email"]))
    return out


def personalize(template, email, key):
    """Fill the per-recipient bits of a built feature email: {{email}} and {{unsubscribe}} (their own signed link)."""
    return template.replace("{{unsubscribe}}", off_url(key)).replace("{{email}}", email)


def send_all(subject, html_tpl, text_tpl, apply=False):
    """Send one built feature email to every recipient(), each with their own unsubscribe link and the RFC 8058
    List-Unsubscribe headers. Dry run unless apply=True. Returns (sent, failed, skipped)."""
    users = accounts._read()["users"]
    todo = recipients(users)
    sent = failed = 0
    for key, email in todo:
        if not apply:
            continue
        msg = accounts.compose_mail(email, accounts.MAIL_FROM, subject, personalize(text_tpl, email, key), personalize(html_tpl, email, key))
        if accounts.deliver_mail(_with_unsubscribe_headers(msg, key)):
            sent += 1
        else:
            failed += 1
        time.sleep(1)
    return sent, failed, len(users) - len(todo)


if __name__ == "__main__":
    # python3 featuremail.py SUBJECT HTML_FILE TEXT_FILE [--apply]   (dry run lists the count only)
    import sys
    if len(sys.argv) < 4:
        sys.exit("usage: featuremail.py SUBJECT HTML_FILE TEXT_FILE [--apply]")
    subj, hp, tp = sys.argv[1:4]
    res = send_all(subj, open(hp, encoding="utf-8").read(), open(tp, encoding="utf-8").read(), apply="--apply" in sys.argv)
    print("%s: would send %d, skipped %d" % ("DRY RUN", len(recipients()), res[2]) if "--apply" not in sys.argv
          else "sent %d, failed %d, skipped %d" % res)
