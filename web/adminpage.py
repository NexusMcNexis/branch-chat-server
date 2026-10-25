"""Administrator page for Branch Chat Server (0.23.3-beta.15 / 0.22.8-public.13, Oct 18 2026).

What this module does, in plain words:
  * Describes every setting the server reads from its settings file (SCHEMA), with a label and an
    explanation, so the Administrator page can show them as a form.
  * Reads the settings file, shows each value (secrets are never sent to the browser, only "set" or
    "not set"), validates changes, keeps a timestamped backup of the old file and writes the new file
    atomically when the administrator presses Apply. Then it restarts the server in place so the new
    values take effect, and puts the old file back automatically if the server cannot start.
  * Guards the page with a short-lived "admin session" (15 minutes). To get one, the owner proves it is
    really them again: either by signing in again (password, plus the authenticator code when two-factor
    is on, plus an emailed code when this browser was never verified), or by approving the request on
    another device where they are already signed in and verified (the same "trusted browser" mark the
    normal sign-in uses).

Only the server owner (BC_OWNER) is the administrator. server.py checks that on every request; this
module never trusts the browser for it. Standard library only.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import accounts
import bcconfig

# How long an admin session lasts after re-verification. Short on purpose: the page holds powerful tools.
ADMIN_SECONDS = 15 * 60
# How long a "approve on another device" request waits for an answer.
APPROVAL_SECONDS = 120
# Wrong passwords / codes allowed on the admin check before it locks for LOCK_SECONDS.
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
COOKIE = "bc_admin"
GUARD_ENV = "BC_ADMIN_APPLY_GUARD"      # set only across the in-place restart after Apply
SECRET_NAME_RE = re.compile(r"(PASS|SECRET|TOKEN|PRIVATE|_KEY$|_KEYS$|PUBKEYS)")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

_lock = threading.Lock()
_pending = {}      # session id -> {"stage": "totp"|"email", "exp": ts}   (re-login in progress)
_approvals = {}    # request id -> {"sid", "user", "code", "exp", "status", "device"}
_fails = {}        # username_key -> [timestamps of failed tries]
_hooks = {}        # functions from server.py (email codes, authenticator codes); see init()
STARTED_AT = int(time.time())


def init(**hooks):
    """server.py hands over its sign-in helpers here, so this module reuses them instead of copying them."""
    _hooks.update(hooks)


# ---------------------------------------------------------------------------
# Settings schema. One entry per setting. "type" decides how the page shows it and how it is checked.
#   readonly: the page shows the value but will not change it (with the reason), because a wrong value
#             there can make the server unreachable or open a network door; edit the file by hand.
# Only settings this server's code actually reads are shown (see known_keys()), so the same table works
# for the family line and the public line.
# ---------------------------------------------------------------------------
G_ADDR, G_LOOK, G_OWNER, G_MAIL, G_DIR, G_PUBLIC, G_LIMITS, G_LINKS, G_ADV = (
    "Server address", "Name and look", "Owner", "Email", "Directory listing", "Sign-up and safety",
    "Limits", "Downloads and links", "Advanced (installer settings)")
GROUP_ORDER = [G_ADDR, G_LOOK, G_OWNER, G_MAIL, G_DIR, G_PUBLIC, G_LIMITS, G_LINKS, G_ADV]
_RO_NET = "Changing this can make the server unreachable. Edit the settings file by hand and restart."
_RO_DOOR = "This opens a network listener for the admin panel. Edit the settings file by hand."

# 26.10.2: the "default" values below mirror bcconfig.py's 26.10 defaults (localhost address, "admin" owner,
# noreply@localhost sender), so the page never shows one particular server's address or a personal email.
SCHEMA = [
    # --- address
    {"key": "BC_PUBLIC_URL", "group": G_ADDR, "type": "url", "label": "Public address",
     "help": "The web address people open, for example https://chat.example.com. HTTPS is needed for encryption, notifications and installing the app.",
     "default": "http://localhost:8766"},
    {"key": "BC_HOSTS", "group": G_ADDR, "type": "hosts", "label": "Extra host names",
     "help": "Other names or IP addresses this server answers to, separated by commas. The public address is always allowed.", "default": ""},
    {"key": "BC_TRUSTED_PROXIES", "group": G_ADDR, "type": "cidrs", "label": "Trusted proxies",
     "help": "Networks (like 172.16.0.0/12) whose forwarded-for header is believed, so rate limits see the real visitor. Leave empty unless you run behind a proxy.", "default": ""},
    # --- name and look
    {"key": "BC_INSTANCE_NAME", "group": G_LOOK, "type": "text", "label": "Group or family name",
     "help": "Shown in page titles, the home-screen app name and invite emails. Empty means plain BranchChat.", "default": ""},
    {"key": "BC_ORG_NAME", "group": G_LOOK, "type": "text", "label": "Organisation name",
     "help": "Shown on the sign-in screen, the top banner and in the directory. Falls back to the group name.", "default": ""},
    {"key": "BC_ORG_ICON", "group": G_LOOK, "type": "path", "label": "Organisation picture",
     "help": "Full path of a png, jpg or webp file on the server (64 KB max). It replaces the BranchChat icon in the top corner and the browser tab.", "default": ""},
    {"key": "BC_ORG_TYPE", "group": G_LOOK, "type": "enum", "label": "Organisation type",
     "help": "Lets the directory show a tag such as Family or Church.",
     "options": ["", "family", "church", "school", "club", "business", "community"], "default": ""},
    {"key": "BC_ACCENT", "group": G_LOOK, "type": "color", "label": "Accent colour",
     "help": "Main colour of the default theme, as #rrggbb. People can still pick their own theme.", "default": ""},
    {"key": "BC_BACKGROUND", "group": G_LOOK, "type": "path", "label": "Background picture",
     "help": "A file name inside the server's branding folder, or a full path. Empty keeps the normal backgrounds.", "default": ""},
    # --- owner
    {"key": "BC_OWNER", "group": G_OWNER, "type": "username", "label": "Owner account",
     "help": "The one account that gets the Administrator page and the owner tools. If you change it, you lose access and the new owner gets it.",
     "default": "admin", "danger": True},
    # --- mail
    {"key": "BC_MAIL_MODE", "group": G_MAIL, "type": "enum", "label": "How mail is sent",
     "help": "sendmail uses the server's mail program, smtp uses a mail provider such as Postmark, log prints codes to the server log (testing only).",
     "options": ["", "sendmail", "smtp", "log"], "default": "sendmail (smtp when a mail server is set)"},
    {"key": "BC_MAIL_FROM", "group": G_MAIL, "type": "mailfrom", "label": "From address",
     "help": "Who invite and sign-in emails come from, for example BranchChat <noreply@example.com>.", "default": "BranchChat <noreply@localhost>"},
    {"key": "BC_SENDMAIL", "group": G_MAIL, "type": "path", "label": "Sendmail program",
     "help": "Path of the sendmail program, used when mail is sent with sendmail.", "default": "/usr/sbin/sendmail"},
    {"key": "BC_SMTP_HOST", "group": G_MAIL, "type": "host", "label": "Mail server (SMTP)",
     "help": "Host name of the mail provider, for example smtp.postmarkapp.com.", "default": ""},
    {"key": "BC_SMTP_PORT", "group": G_MAIL, "type": "int", "min": 1, "max": 65535, "label": "Mail server port",
     "help": "587 for STARTTLS (most providers), 465 for SSL.", "default": "587"},
    {"key": "BC_SMTP_TLS", "group": G_MAIL, "type": "enum", "label": "Mail encryption",
     "help": "starttls for port 587, ssl for port 465, none only for a trusted relay on your own network.",
     "options": ["", "starttls", "ssl", "none"], "default": "starttls (ssl on port 465)"},
    {"key": "BC_SMTP_USER", "group": G_MAIL, "type": "text", "label": "Mail user name",
     "help": "The user name the mail provider gave you.", "default": ""},
    {"key": "BC_SMTP_PASSWORD", "group": G_MAIL, "type": "secret", "label": "Mail password",
     "help": "Stored in the settings file. A password file (next setting) is safer. This page never shows it, only whether it is set.", "default": ""},
    {"key": "BC_SMTP_PASSWORD_FILE", "group": G_MAIL, "type": "path", "label": "Mail password file",
     "help": "Full path of a file that holds only the mail password, readable by the server.", "default": ""},
    {"key": "BC_INVITE_NOTE", "group": G_MAIL, "type": "text", "label": "Extra sentence in invites",
     "help": "Added to invite emails. Empty adds nothing.", "default": "It is separate from the encode page."},
    # --- directory
    {"key": "BC_DIRECTORY_REPORT", "group": G_DIR, "type": "bool", "label": "List this server in the directory",
     "help": "Sends a signed 'I'm online' note to the directory home every few minutes, so the server is listed there.", "default": "0"},
    {"key": "BC_HOME_URL", "group": G_DIR, "type": "url", "label": "Directory home",
     "help": "The directory this server reports to.", "default": "https://branchchat.app"},
    {"key": "BC_DIRECTORY_PING_SECONDS", "group": G_DIR, "type": "int", "min": 60, "max": 86400, "label": "Seconds between directory notes",
     "help": "How often the online note is sent. At least 60.", "default": "300"},
    {"key": "BC_HOME_MODE", "group": G_DIR, "type": "bool", "label": "Be a directory home",
     "help": "Only for a server that hosts the directory itself (branchchat.app).", "default": "0"},
    {"key": "BC_DIRECTORY_TTL", "group": G_DIR, "type": "int", "min": 30, "max": 86400, "label": "Listing lifetime (seconds)",
     "help": "Directory home only: how long a listing stays without a fresh note.", "default": "900"},
    {"key": "BC_DIRECTORY_ALLOW_HTTP", "group": G_DIR, "type": "bool", "label": "Allow plain http servers",
     "help": "Directory home only, for testing.", "default": "0"},
    {"key": "BC_DIRECTORY_ALLOW_PRIVATE", "group": G_DIR, "type": "bool", "label": "Allow private-network servers",
     "help": "Directory home only, for testing.", "default": "0"},
    {"key": "BC_DIRECTORY_ADMIN_LISTEN", "group": G_DIR, "type": "text", "label": "Admin panel listener", "readonly": _RO_DOOR,
     "help": "Directory home only: address of the LAN-only admin API.", "default": ""},
    {"key": "BC_DIRECTORY_ADMIN_ALLOW", "group": G_DIR, "type": "text", "label": "Admin panel allowed addresses", "readonly": _RO_DOOR,
     "help": "Directory home only.", "default": ""},
    {"key": "BC_ADMIN_PANEL_PUBKEYS", "group": G_DIR, "type": "text", "label": "Admin panel keys", "readonly": _RO_DOOR,
     "help": "Directory home only.", "default": ""},
    # --- public line: sign-up and safety
    {"key": "BC_PUBLIC_MODE", "group": G_PUBLIC, "type": "bool", "label": "Public server",
     "help": "Turns on the public-server defaults below (open sign-up, filters, report and block).", "default": "0"},
    {"key": "BC_FRONTDOOR", "group": G_PUBLIC, "type": "bool", "label": "Front door page",
     "help": "Shows the welcome front door to visitors who are not signed in.", "default": "0"},
    {"key": "BC_OPEN_SIGNUP", "group": G_PUBLIC, "type": "bool", "label": "Open sign-up",
     "help": "Lets anyone create an account. Keep it off until email works.", "default": "same as Public server", "danger": True},
    {"key": "BC_REPORT_BLOCK", "group": G_PUBLIC, "type": "bool", "label": "Report and block",
     "help": "Lets people report messages and block other people.", "default": "same as Public server"},
    # 26.10.3: the BranchChat Helper welcome bot (web/welcomebot.py). Its words live in welcome-bot.json beside this file.
    {"key": "BC_WELCOME_BOT", "group": G_PUBLIC, "type": "bool", "label": "Helper bot for new people",
     "help": "Gives every account a friendly Helper bot friend with a welcome and a short guided tour. "
             "Edit its words in welcome-bot.json next to this settings file.", "default": "off"},
    {"key": "BC_WELCOME_BOT_NAME", "group": G_PUBLIC, "type": "text", "label": "Helper bot name",
     "help": "The name people see for the Helper bot.", "default": "BranchChat Helper"},
    # 26.10.2: the group chats switch (server.py GROUPS_ENABLED). On by default; off stops new groups, adding people and
    # group invite links, while existing groups keep working, so people never lose a conversation.
    {"key": "BC_GROUPS", "group": G_PUBLIC, "type": "bool", "label": "Group chats",
     "help": "Lets people start group chats and invite others to them. Turning it off keeps existing groups working.", "default": "1"},
    {"key": "BC_LOBBY_LINK_FILTER", "group": G_PUBLIC, "type": "bool", "label": "Lobby link filter",
     "help": "Blocks links in the Lobby.", "default": "same as Public server"},
    {"key": "BC_LANGUAGE_FILTER", "group": G_PUBLIC, "type": "bool", "label": "Language filter",
     "help": "Lets people hide mature language.", "default": "same as Public server"},
    {"key": "BC_LANGUAGE_FILTER_DEFAULT", "group": G_PUBLIC, "type": "bool", "label": "Language filter on by default",
     "help": "New accounts start with the language filter on.", "default": "same as Public server"},
    {"key": "BC_LANGUAGE_LOCK", "group": G_PUBLIC, "type": "bool", "label": "Lock the language filter on",
     "help": "Heavy mature language is always filtered and people cannot turn it off.", "default": "same as Public server"},
    {"key": "BC_FULL_ACCOUNT_DELETE", "group": G_PUBLIC, "type": "bool", "label": "Full account delete",
     "help": "Deleting an account also removes that person's messages and files.", "default": "same as Public server"},
    {"key": "BC_LOGIN_LIMITS", "group": G_PUBLIC, "type": "bool", "label": "Sign-in limits",
     "help": "Slows down repeated wrong passwords.", "default": "same as Public server"},
    {"key": "BC_FILTER_EVASION_LIMIT", "group": G_PUBLIC, "type": "int", "min": 1, "max": 1000, "label": "Filter dodges before a mute",
     "help": "How many filtered messages in the window before a short mute.", "default": "3"},
    {"key": "BC_FILTER_EVASION_WINDOW_MIN", "group": G_PUBLIC, "type": "int", "min": 1, "max": 1440, "label": "Filter dodge window (minutes)", "help": "", "default": "10"},
    {"key": "BC_FILTER_STRIP_LIMIT", "group": G_PUBLIC, "type": "int", "min": 1, "max": 1000, "label": "Stripped links before a mute", "help": "", "default": "5"},
    {"key": "BC_FILTER_STRIP_WINDOW_MIN", "group": G_PUBLIC, "type": "int", "min": 1, "max": 1440, "label": "Stripped link window (minutes)", "help": "", "default": "10"},
    {"key": "BC_FILTER_MUTE_MIN", "group": G_PUBLIC, "type": "int", "min": 0, "max": 10080, "label": "Mute length (minutes)", "help": "", "default": "30"},
    {"key": "BC_TURNSTILE_SITEKEY", "group": G_PUBLIC, "type": "text", "label": "Cloudflare Turnstile site key",
     "help": "Bot check on the sign-up form. Needs the secret below.", "default": ""},
    {"key": "BC_TURNSTILE_SECRET", "group": G_PUBLIC, "type": "secret", "label": "Cloudflare Turnstile secret",
     "help": "Never shown on this page.", "default": ""},
    # --- limits
    {"key": "BC_INVITE_PER_USER_HOUR", "group": G_LIMITS, "type": "int", "min": 1, "max": 10000, "label": "Invites per person per hour", "help": "", "default": "10"},
    {"key": "BC_INVITE_PER_USER_DAY", "group": G_LIMITS, "type": "int", "min": 1, "max": 100000, "label": "Invites per person per day", "help": "", "default": "30"},
    {"key": "BC_INVITE_PER_IP_HOUR", "group": G_LIMITS, "type": "int", "min": 1, "max": 10000, "label": "Invites per address per hour", "help": "", "default": "20"},
    {"key": "BC_INVITE_PER_IP_DAY", "group": G_LIMITS, "type": "int", "min": 1, "max": 100000, "label": "Invites per address per day", "help": "", "default": "60"},
    {"key": "BC_REDEEM_PER_IP_HOUR", "group": G_LIMITS, "type": "int", "min": 1, "max": 10000, "label": "Invite links opened per address per hour", "help": "", "default": "30"},
    {"key": "BC_REDEEM_PER_IP_DAY", "group": G_LIMITS, "type": "int", "min": 1, "max": 100000, "label": "Invite links opened per address per day", "help": "", "default": "100"},
    {"key": "BC_SIGNUP_PER_IP_HOUR", "group": G_LIMITS, "type": "int", "min": 0, "max": 10000, "label": "Sign-ups per address per hour", "help": "", "default": "3"},
    {"key": "BC_SIGNUP_PER_IP_DAY", "group": G_LIMITS, "type": "int", "min": 0, "max": 100000, "label": "Sign-ups per address per day", "help": "", "default": "10"},
    {"key": "BC_SIGNUP_GLOBAL_HOUR", "group": G_LIMITS, "type": "int", "min": 0, "max": 100000, "label": "Sign-ups per hour (everyone)", "help": "", "default": "30"},
    {"key": "BC_SIGNUP_GLOBAL_DAY", "group": G_LIMITS, "type": "int", "min": 0, "max": 1000000, "label": "Sign-ups per day (everyone)", "help": "", "default": "300"},
    {"key": "BC_LOGIN_FAILS_PER_IP", "group": G_LIMITS, "type": "int", "min": 1, "max": 10000, "label": "Wrong sign-ins per address", "help": "", "default": "20"},
    {"key": "BC_LOGIN_FAILS_PER_USER", "group": G_LIMITS, "type": "int", "min": 1, "max": 10000, "label": "Wrong sign-ins per account", "help": "", "default": "10"},
    # --- downloads and links (public line)
    {"key": "BC_DOWNLOAD_LINKS", "group": G_LINKS, "type": "bool", "label": "Show app download links", "help": "", "default": "0"},
    {"key": "BC_HOST_LINK", "group": G_LINKS, "type": "bool", "label": "'Host your own' link", "help": "Shows the link to the server download page.", "default": "same as Public server"},
    {"key": "BC_SERVER_RELEASES_URL", "group": G_LINKS, "type": "url", "label": "Server download page", "help": "",
     "default": "https://github.com/NexusMcNexis/branch-chat-server/releases/latest"},
    {"key": "BC_SERVER_RELEASES_ALL_URL", "group": G_LINKS, "type": "url", "label": "All server releases page", "help": "", "default": ""},
    {"key": "BC_SERVER_DOCS_URL", "group": G_LINKS, "type": "url", "label": "Server guide page", "help": "", "default": ""},
    {"key": "BC_HOST_CHECK_SECONDS", "group": G_LINKS, "type": "int", "min": 300, "max": 604800, "label": "Release check interval (seconds)", "help": "", "default": "3600"},
    {"key": "BC_APK_FILE", "group": G_LINKS, "type": "text", "label": "Android app file", "help": "File name of the APK in the downloads folder.", "default": ""},
    {"key": "BC_APK_BETA", "group": G_LINKS, "type": "bool", "label": "Mark the Android app as beta", "help": "", "default": "1"},
    {"key": "BC_DOWNLOAD_DIR", "group": G_LINKS, "type": "path", "label": "Downloads folder", "help": "", "default": "(state folder)/downloads"},
    {"key": "BC_PLAY_URL", "group": G_LINKS, "type": "url", "label": "Google Play link", "help": "", "default": ""},
    {"key": "BC_APPSTORE_URL", "group": G_LINKS, "type": "url", "label": "App Store link", "help": "", "default": ""},
    {"key": "BC_WINDOWS_URL", "group": G_LINKS, "type": "url", "label": "Windows download link", "help": "", "default": ""},
    {"key": "BC_MACOS_URL", "group": G_LINKS, "type": "url", "label": "macOS download link", "help": "", "default": ""},
    {"key": "BC_LINUX_URL", "group": G_LINKS, "type": "url", "label": "Linux download page", "help": "", "default": ""},
    {"key": "BC_LINUX_DEB_URL", "group": G_LINKS, "type": "url", "label": "Linux .deb link", "help": "", "default": ""},
    {"key": "BC_LINUX_RPM_URL", "group": G_LINKS, "type": "url", "label": "Linux .rpm link", "help": "", "default": ""},
    {"key": "BC_LINUX_APPIMAGE_URL", "group": G_LINKS, "type": "url", "label": "Linux AppImage link", "help": "", "default": ""},
    {"key": "BC_LINUX_APPIMAGE_ARM64_URL", "group": G_LINKS, "type": "url", "label": "Linux AppImage (ARM) link", "help": "", "default": ""},
    {"key": "BC_LINUX_FLATPAK_URL", "group": G_LINKS, "type": "url", "label": "Linux Flatpak link", "help": "", "default": ""},
    {"key": "BC_LINUX_TAR_URL", "group": G_LINKS, "type": "url", "label": "Linux .tar link", "help": "", "default": ""},
    {"key": "BC_LINUX_TAR_SHA256", "group": G_LINKS, "type": "hex64", "label": "Linux .tar checksum", "help": "SHA-256 of the .tar file.", "default": ""},
    {"key": "BC_LINUX_TAR_ARM64_URL", "group": G_LINKS, "type": "url", "label": "Linux .tar (ARM) link", "help": "", "default": ""},
    {"key": "BC_LINUX_TAR_ARM64_SHA256", "group": G_LINKS, "type": "hex64", "label": "Linux .tar (ARM) checksum", "help": "", "default": ""},
    # --- installer settings: shown, not editable here
    {"key": "BC_HOST", "group": G_ADV, "type": "text", "label": "Listen address", "readonly": _RO_NET, "help": "", "default": "127.0.0.1"},
    {"key": "BC_PORT", "group": G_ADV, "type": "int", "label": "Listen port", "readonly": _RO_NET, "help": "", "default": "8766"},
    {"key": "BC_STATE_DIR", "group": G_ADV, "type": "path", "label": "Data folder", "readonly": _RO_NET, "help": "Where accounts and messages live.", "default": "/var/lib/huzz-chat"},
    {"key": "BC_APP_DIR", "group": G_ADV, "type": "path", "label": "Program folder", "readonly": _RO_NET, "help": "", "default": "(folder of server.py)"},
]
_BY_KEY = {row["key"]: row for row in SCHEMA}
_known_cache = None


def known_keys():
    """Setting names this server's own code reads, found by scanning its .py files once.
    Why: the family line and the public line read different settings; this keeps one table for both."""
    global _known_cache
    if _known_cache is None:
        found = set()
        here = os.path.dirname(os.path.abspath(__file__))
        for name in os.listdir(here):
            if name.endswith(".py") and name != os.path.basename(__file__):
                try:
                    with open(os.path.join(here, name), "r", encoding="utf-8") as fh:
                        found.update(re.findall(r"BC_[A-Z0-9_]+", fh.read()))
                except OSError:
                    pass
        _known_cache = found
    return _known_cache


# ---------------------------------------------------------------------------
# Where the settings file lives
# ---------------------------------------------------------------------------
def in_docker():
    """True inside a container (the Docker image sets BC_STATE_DIR=/data and has /.dockerenv)."""
    return os.path.exists("/.dockerenv") or bcconfig.STATE_DIR == "/data"


def config_path():
    """The file the page edits: the one the server read at start, or the standard place for this install."""
    if bcconfig.CONFIG_PATH:
        return bcconfig.CONFIG_PATH
    if in_docker():
        return os.path.join(bcconfig.STATE_DIR, "config.env")
    return bcconfig.DEFAULT_CONFIG_PATH


def _dir_writable(path):
    d = os.path.dirname(path) or "."
    return os.path.isdir(d) and os.access(d, os.W_OK)


def file_writable(path):
    """Can the server save this file? (Either replace it atomically, or at least rewrite it in place.)"""
    if os.path.exists(path):
        return _dir_writable(path) or os.access(path, os.W_OK)
    return _dir_writable(path)


def backup_dir(path):
    """Backups go next to the settings file (config-backups/), or into the data folder when that is not writable."""
    near = os.path.join(os.path.dirname(path), "config-backups")
    if os.path.isdir(near) and os.access(near, os.W_OK):
        return near
    if not os.path.exists(near) and _dir_writable(path):
        return near
    return os.path.join(bcconfig.STATE_DIR, "config-backups")


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def text_hash(text):
    """Fingerprint of the file as the page saw it; Apply refuses if the file changed meanwhile."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_secret_key(key):
    row = _BY_KEY.get(key)
    if row:
        return row["type"] == "secret"
    return bool(SECRET_NAME_RE.search(key))


# ---------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------
def config_view():
    """Everything the settings form needs. Secret values never leave the server: only set / not set."""
    path = config_path()
    text = read_text(path)
    file_vals = bcconfig.parse_env_file(path) if text else {}
    known = known_keys()
    rows = []
    for row in SCHEMA:
        key = row["key"]
        if key not in known and key not in file_vals and key not in os.environ:
            continue
        out = {k: row[k] for k in ("key", "group", "type", "label", "help", "default") if k in row}
        for extra in ("options", "min", "max", "danger"):
            if extra in row:
                out[extra] = row[extra]
        _fill_value(out, key, file_vals)
        if row.get("readonly"):
            out["readonly"] = row["readonly"]
        rows.append(out)
    # Settings in the file that this table does not describe: still shown and editable as plain text.
    for key in file_vals:
        if key in _BY_KEY:
            continue
        out = {"key": key, "group": "Other settings in the file", "type": "secret" if is_secret_key(key) else "text",
               "label": key, "help": "Not described by this version of BranchChat. It is kept as it is unless you change it.", "default": ""}
        _fill_value(out, key, file_vals)
        rows.append(out)
    return {
        "path": path,
        "exists": bool(text),
        "writable": file_writable(path),
        "backup_dir": backup_dir(path),
        "docker": in_docker(),
        "hash": text_hash(text),
        "groups": GROUP_ORDER + ["Other settings in the file"],
        "settings": rows,
        "backups": list_backups(path)[:10],
    }


def _fill_value(out, key, file_vals):
    """Add where the value comes from (environment / file / default) and the value itself (masked for secrets)."""
    secret = out["type"] == "secret"
    if key in os.environ:
        out["source"] = "environment"
        # Environment wins over the file, so the page cannot change it; the host sets it (systemd or docker env_file).
        out["readonly"] = out.get("readonly") or (
            "Set by the server's environment (%s), which wins over this file. Change it there." %
            ("the Docker env_file" if in_docker() else "the service settings"))
        raw = os.environ.get(key, "")
    elif key in file_vals:
        out["source"] = "file"
        raw = file_vals[key]
    else:
        out["source"] = "default"
        raw = None
    if secret:
        out["is_set"] = bool(raw)
        out["value"] = None
    else:
        out["value"] = raw


def list_backups(path):
    """Newest first: file names of earlier versions of the settings file."""
    d = backup_dir(path)
    base = os.path.basename(path)
    try:
        names = [n for n in os.listdir(d) if n.startswith(base + ".")]
    except OSError:
        return []
    return sorted(names, reverse=True)


# ---------------------------------------------------------------------------
# Checking values
# ---------------------------------------------------------------------------
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _check_url(v):
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in v:
        return "Enter a full web address starting with https://"
    return ""


def _check_host_item(item):
    try:
        ipaddress.ip_address(item)
        return True
    except ValueError:
        pass
    return bool(_HOST_RE.match(item))


def validate(key, value, accounts_find=None):
    """Return (clean value, error text). Empty value always means 'remove the line, use the default'."""
    row = _BY_KEY.get(key) or {"type": "secret" if is_secret_key(key) else "text"}
    if not isinstance(value, str):
        return None, "That value is not text."
    if _CTRL.search(value):
        return None, "Line breaks and control characters are not allowed."
    value = value.strip()
    if len(value) > 2000:
        return None, "That value is too long."
    if value == "":
        return "", ""
    kind = row["type"]
    if kind == "bool":
        low = value.lower()
        if low in ("1", "true", "yes", "on"):
            return "1", ""
        if low in ("0", "false", "no", "off"):
            return "0", ""
        return None, "Choose on or off."
    if kind == "int":
        if not re.fullmatch(r"\d{1,9}", value):
            return None, "Enter a whole number."
        n = int(value)
        if "min" in row and n < row["min"]:
            return None, "Use %d or more." % row["min"]
        if "max" in row and n > row["max"]:
            return None, "Use %d or less." % row["max"]
        return str(n), ""
    if kind == "enum":
        if value not in row.get("options", []):
            return None, "Choose one of: " + ", ".join(o for o in row.get("options", []) if o)
        return value, ""
    if kind == "url":
        err = _check_url(value)
        return (None, err) if err else (value.rstrip("/") if key in ("BC_PUBLIC_URL", "BC_HOME_URL") else value, "")
    if kind == "color":
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return None, "Use a colour like #3c92ea."
        return value.lower(), ""
    if kind == "hosts":
        items = [i.strip() for i in value.replace(" ", ",").split(",") if i.strip()]
        bad = [i for i in items if not _check_host_item(i)]
        return (None, "Not a host name or IP: " + ", ".join(bad[:3])) if bad else (",".join(items), "")
    if kind == "host":
        return (value, "") if _check_host_item(value) else (None, "Enter a host name like smtp.example.com.")
    if kind == "cidrs":
        items = [i.strip() for i in value.split(",") if i.strip()]
        for i in items:
            try:
                ipaddress.ip_network(i, strict=False)
            except ValueError:
                return None, "Not a network: " + i
        return ",".join(items), ""
    if kind == "hex64":
        return (value.lower(), "") if re.fullmatch(r"[0-9a-fA-F]{64}", value) else (None, "Enter the 64-character SHA-256.")
    if kind == "username":
        found = accounts_find(value) if accounts_find else None
        if not found or found.get("disabled") or not found.get("password"):
            return None, "There is no active account with that username."
        return (found.get("username_key") or value).lower(), ""
    if kind == "mailfrom":
        if "@" not in value or len(value) > 300:
            return None, "Use a name and address, like BranchChat <noreply@example.com>."
        return value, ""
    if kind == "path":
        if len(value) > 400:
            return None, "That path is too long."
        return value, ""
    # text / secret
    return value, ""


# ---------------------------------------------------------------------------
# Writing the file
# ---------------------------------------------------------------------------
_SAFE_BARE = re.compile(r"^[A-Za-z0-9_./:@,+%=#-]*$")


def quote_value(value):
    """Write a value so bcconfig.parse_env_file reads back exactly the same text.
    Simple values stay bare; anything else is double-quoted with \\ \" and \\$ escaped."""
    if _SAFE_BARE.match(value) and not value.startswith(("'", '"')):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _line_key(raw):
    """The KEY of an active KEY=VALUE line (same rules as bcconfig.parse_env_file), else None."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    key, sep, _ = line.partition("=")
    key = key.strip()
    if not sep or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        return None
    return key


def render(old_text, changes, who):
    """New file text: existing lines and comments are kept; changed keys are edited in place;
    removed keys lose their line; new keys go at the end with a short comment explaining them."""
    lines = old_text.splitlines()
    out = []
    done = set()
    # Find the last line for each key we change, so duplicates collapse into that one.
    last = {}
    for i, raw in enumerate(lines):
        k = _line_key(raw)
        if k in changes:
            last[k] = i
    for i, raw in enumerate(lines):
        k = _line_key(raw)
        if k in changes:
            if last.get(k) != i:
                continue              # an earlier duplicate of a key we are setting: drop it
            if changes[k] == "":
                done.add(k)
                continue              # removed: the server uses its default
            out.append("%s=%s" % (k, quote_value(changes[k])))
            done.add(k)
            continue
        out.append(raw)
    added = [(k, v) for k, v in changes.items() if k not in done and v != ""]
    if not old_text.strip():
        out = [
            "# BranchChat Server settings. KEY=VALUE, one per line. Lines starting with # are ignored.",
            "# Edit here or on the Administrator page (profile menu > Administrator); the page keeps a backup of",
            "# every earlier version in config-backups/. Restart the server after editing by hand.",
            "# Full annotated list of settings: server/config.env.example in the BranchChat source.",
        ]
    if added:
        out.append("")
        out.append("# Added on the Administrator page by %s, %s" % (who, time.strftime("%Y-%m-%d %H:%M %Z")))
        for k, v in added:
            row = _BY_KEY.get(k)
            if row:
                out.append(("# %s. %s" % (row["label"], row.get("help", ""))).rstrip())
            out.append("%s=%s" % (k, quote_value(v)))
    return "\n".join(out).rstrip("\n") + "\n"


def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_backup(path, text):
    """Copy of the current file as config-backups/config.env.YYYYMMDD-HHMMSS (mode 600). Returns its path."""
    d = backup_dir(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(d, "%s.%s" % (os.path.basename(path), stamp))
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(d, "%s.%s-%d" % (os.path.basename(path), stamp, n))
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    return dest


def write_atomic(path, text):
    """Replace the file in one step (temp file + rename), keeping its owner and mode.
    When the folder is not writable (hardened native installs) the file is rewritten in place instead."""
    d = os.path.dirname(path) or "."
    try:
        st = os.stat(path)
        mode, uid, gid = st.st_mode & 0o777, st.st_uid, st.st_gid
    except FileNotFoundError:
        mode, uid, gid = 0o600, None, None
    if _dir_writable(path):
        fd, tmp = tempfile.mkstemp(prefix=".config.env.", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, mode)
            if uid is not None:
                try:
                    os.chown(tmp, uid, gid)
                except PermissionError:
                    pass
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _fsync_dir(d)
        return "replaced"
    with open(path, "r+", encoding="utf-8") as fh:
        fh.seek(0)
        fh.write(text)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    return "rewritten"


def apply(changes, base_hash, who, accounts_find):
    """Check every change, back up the file, write it. Returns (result dict, http status)."""
    if not isinstance(changes, dict) or not changes:
        return {"error": "There are no changes to apply."}, 400
    if len(changes) > 200:
        return {"error": "Too many changes at once."}, 400
    path = config_path()
    if not file_writable(path):
        return {"error": "The server cannot write %s. Fix its permissions, or edit it by hand." % path}, 409
    old = read_text(path)
    if base_hash != text_hash(old):
        return {"error": "The settings file changed since this page loaded. Reload to see the new values.", "stale": True}, 409
    view = {r["key"]: r for r in config_view()["settings"]}
    clean, errors = {}, {}
    for key, value in changes.items():
        if not isinstance(key, str) or not _KEY_RE.match(key):
            errors[str(key)[:64]] = "Unknown setting."
            continue
        row = view.get(key)
        if row is None:
            errors[key] = "Unknown setting."
            continue
        if row.get("readonly"):
            errors[key] = row["readonly"]
            continue
        value = "" if value is None else value
        v, err = validate(key, value, accounts_find)
        if err:
            errors[key] = err
        else:
            clean[key] = v
    if errors:
        return {"error": "Some values need fixing.", "fields": errors}, 400
    new = render(old, clean, who)
    # Read the new text back the same way the server will, and make sure every value comes back exactly.
    fd, probe = tempfile.mkstemp(prefix="bc-config-check-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new)
        parsed = bcconfig.parse_env_file(probe)
    finally:
        os.unlink(probe)
    for key, v in clean.items():
        if (v == "" and key in parsed) or (v != "" and parsed.get(key) != v):
            return {"error": "The new file did not read back correctly; nothing was changed."}, 500
    backup = write_backup(path, old) if old else ""
    how = write_atomic(path, new)
    return {"ok": True, "path": path, "backup": backup, "write": how, "changed": sorted(clean),
            "hash": text_hash(new)}, 200


# ---------------------------------------------------------------------------
# Restart in place after Apply, with automatic undo if the new settings stop the server from starting
# ---------------------------------------------------------------------------
def restart_soon(path, backup, delay=1.2):
    """Re-run this same program (same process id, so systemd and Docker see no change) after the reply is sent.
    The guard variable tells the new process which backup to restore if it fails to start."""
    def go():
        time.sleep(delay)
        guard = json.dumps({"path": path, "backup": backup, "at": int(time.time())})
        env = dict(os.environ)
        env[GUARD_ENV] = guard
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    threading.Thread(target=go, daemon=True).start()


def recover_after_failed_start():
    """Called by server.py when startup raised. If we just applied new settings, put the old file back and try again.
    Returns only when there is nothing to undo (then the caller re-raises the error)."""
    raw = os.environ.pop(GUARD_ENV, "")
    if not raw:
        return
    try:
        guard = json.loads(raw)
        path, backup = guard["path"], guard.get("backup") or ""
        if backup and os.path.isfile(backup):
            with open(backup, "r", encoding="utf-8") as fh:
                write_atomic(path, fh.read())
            print("admin page: the server did not start with the new settings; restored %s from %s" % (path, backup), flush=True)
        elif not backup and os.path.isfile(path):
            os.replace(path, path + ".failed-%s" % time.strftime("%Y%m%d-%H%M%S"))
            print("admin page: the server did not start with the new settings file; moved it aside", flush=True)
    except Exception as exc:  # never hide the original failure behind a second one
        print("admin page: could not undo the settings change: %s" % exc.__class__.__name__, flush=True)
        return
    os.execve(sys.executable, [sys.executable] + sys.argv, dict(os.environ))


def started_ok():
    """Startup finished: forget the guard so a later crash is not mistaken for a bad Apply."""
    if os.environ.pop(GUARD_ENV, ""):
        print("admin page: restarted with the new settings", flush=True)


# ---------------------------------------------------------------------------
# Admin session (the short-lived proof of re-verification)
# ---------------------------------------------------------------------------
def _sid(user):
    """This browser's login id. Old logins without one fall back to their expiry stamp."""
    claims = user.get("_session") or {}
    return str(claims.get("sid") or ("exp:%s" % claims.get("exp")))


def make_admin_cookie(user):
    """Signed cookie, valid 15 minutes, only for this account, this login (session id) and this session version."""
    token = accounts.make_marked_token(user, "admin", ADMIN_SECONDS, {"sid": _sid(user)})
    return "%s=%s; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=%d" % (COOKIE, token, ADMIN_SECONDS)


def clear_admin_cookie():
    return "%s=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0" % COOKIE


def admin_left(cookie_header, user):
    """Seconds left on this browser's admin session (0 = not verified). Checked on the server every time."""
    data = accounts.open_marked_cookie(cookie_header, COOKIE, "admin", user)
    if not data or data.get("sid") != _sid(user):
        return 0
    return max(0, int(data.get("exp") or 0) - int(time.time()))


# --- failure counting, so the admin check cannot be guessed at
def locked_for(user_key):
    now = time.time()
    with _lock:
        rows = [t for t in _fails.get(user_key, []) if now - t < LOCK_SECONDS]
        _fails[user_key] = rows
        if len(rows) >= MAX_FAILS:
            return int(LOCK_SECONDS - (now - rows[0]))
    return 0


def note_fail(user_key):
    with _lock:
        _fails.setdefault(user_key, []).append(time.time())


def clear_fails(user_key):
    with _lock:
        _fails.pop(user_key, None)


def set_pending(user, stage):
    with _lock:
        _pending[_sid(user)] = {"stage": stage, "exp": time.time() + 600, "u": user["username_key"]}


def get_pending(user):
    with _lock:
        row = _pending.get(_sid(user))
        if row and row["exp"] > time.time() and row["u"] == user["username_key"]:
            return row["stage"]
        _pending.pop(_sid(user), None)
    return ""


def drop_pending(user):
    with _lock:
        _pending.pop(_sid(user), None)


# --- approval from another verified device
def _device_label(ua):
    """A short, harmless description of the asking browser for the approval prompt."""
    ua = ua or ""
    os_name = next((n for t, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                                    ("Mac OS", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")) if t in ua), "a device")
    app = next((n for t, n in (("Firefox", "Firefox"), ("Edg/", "Edge"), ("Chrome", "Chrome"), ("Safari", "Safari")) if t in ua), "a browser")
    return "%s on %s" % (app, os_name)


def approval_start(user, ua):
    """Open a request that another signed-in, verified device of the same account can approve. One per login."""
    now = time.time()
    with _lock:
        for rid in [r for r, row in _approvals.items() if row["exp"] < now - 60 or row["sid"] == _sid(user)]:
            _approvals.pop(rid, None)
        rid = secrets.token_hex(16)
        code = "%06d" % secrets.randbelow(1000000)
        _approvals[rid] = {"sid": _sid(user), "user": user["username_key"], "code": code, "exp": now + APPROVAL_SECONDS,
                           "status": "pending", "device": _device_label(ua), "at": int(now)}
    return rid, code


def approval_for(user, verified_browser):
    """For /api/sync: the request (if any) this device may approve. Not shown on the asking device itself,
    and only on browsers that already passed the email check (the trusted-browser mark)."""
    if not verified_browser:
        return None
    now = time.time()
    with _lock:
        for rid, row in _approvals.items():
            if row["user"] == user["username_key"] and row["status"] == "pending" and row["exp"] > now and row["sid"] != _sid(user):
                return {"id": rid, "code": row["code"], "device": row["device"], "expires_in": int(row["exp"] - now)}
    return None


def approval_decide(user, rid, approve, verified_browser):
    if not verified_browser:
        return "This device is not verified. Sign in again on it first."
    with _lock:
        row = _approvals.get(rid if isinstance(rid, str) else "")
        if not row or row["user"] != user["username_key"] or row["exp"] < time.time() or row["status"] != "pending":
            return "That request expired."
        if row["sid"] == _sid(user):
            return "Approve this from a different device."
        row["status"] = "approved" if approve else "denied"
    return ""


def approval_poll(user, rid):
    """Status for the asking device: pending, approved (it then gets its admin session), denied or expired."""
    with _lock:
        row = _approvals.get(rid if isinstance(rid, str) else "")
        if not row or row["user"] != user["username_key"] or row["sid"] != _sid(user):
            return "expired"
        if row["status"] == "pending" and row["exp"] < time.time():
            row["status"] = "expired"
        status = row["status"]
        if status in ("approved", "denied", "expired"):
            _approvals.pop(rid, None)
        return status


# ---------------------------------------------------------------------------
# Server facts for the overview card
# ---------------------------------------------------------------------------
def overview(version_label):
    """Version, uptime, install type and where things are. No secrets."""
    path = config_path()
    return {
        "version": version_label,
        "started_at": STARTED_AT,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "install": "Docker" if in_docker() else "Native (systemd)",
        "config_path": path,
        "state_dir": bcconfig.STATE_DIR,
        "public_url": bcconfig.PUBLIC_URL,
        "update_how": (
            "Docker: on the host, run the installer's update (sudo ./server/install.sh update) or your deploy script; "
            "it backs up first and rolls back if the new version is unhealthy."
            if in_docker() else
            "Run sudo ./server/install.sh update in the BranchChat folder; it backs up first and rolls back if the new version is unhealthy."
        ),
    }


# ---------------------------------------------------------------------------
# HTTP routes under /api/server-admin/ (server.py calls route() after the sign-in wall)
# ---------------------------------------------------------------------------
def _same_origin(h):
    """Extra cross-site protection for admin POSTs on top of SameSite cookies: JSON only, and the browser's
    Origin / Sec-Fetch-Site (when sent) must say the request came from this server's own pages."""
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return False
    site = (h.headers.get("Sec-Fetch-Site") or "").lower()
    if site and site not in ("same-origin", "none"):
        return False
    origin = h.headers.get("Origin") or ""
    if origin:
        host = (urlsplit(origin).hostname or "").lower()
        if host not in bcconfig.HOSTS and host not in ("127.0.0.1", "localhost"):
            return False
    return True


def _verified_browser(h, user):
    """True when this browser carries the trusted-browser mark from a full sign-in (password + email code)."""
    return accounts.verify_cookie_ok(h.headers.get("Cookie"), user)


def _grant(h, user, extra=None):
    """Re-verification passed: hand this browser a fresh 15-minute admin session."""
    clear_fails(user["username_key"])
    drop_pending(user)
    body = {"ok": True, "verified": True, "expires_in": ADMIN_SECONDS}
    body.update(extra or {})
    h.send_json_extra(200, body, [("Set-Cookie", make_admin_cookie(user))])


def _state(h, user):
    full = accounts.find_user(user["username_key"]) or {}
    return {
        "ok": True,
        "owner": True,
        "username": user["username"],
        "expires_in": admin_left(h.headers.get("Cookie"), user),
        "totp": bool(accounts.totp_enabled(full)),
        "verified_browser": _verified_browser(h, user),
        "locked_for": locked_for(user["username_key"]),
        "stage": get_pending(user),
    }


def route(h, method, path, query, user):
    """Every /api/server-admin/ request. Only the owner gets past the first check; the tools need a live admin session."""
    if not _hooks["is_owner"](user):
        h.send_json(403, {"error": "Only the server administrator can use this."})
        return
    sub = path[len("/api/server-admin/"):]
    if method == "POST" and not _same_origin(h):
        h.send_json(403, {"error": "That request was not accepted."})
        return
    key = user["username_key"]

    # ---- re-verification: no admin session needed yet
    if method == "GET" and sub == "state":
        h.send_json(200, _state(h, user))
        return
    if method == "POST" and sub == "verify/password":
        data = h.read_json_body() or {}
        wait = locked_for(key)
        if wait:
            h.send_json(429, {"error": "Too many wrong tries. Wait %d minutes." % max(1, wait // 60)})
            return
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        checked = accounts.authenticate(user["username"], password) if password else None
        if not checked or checked.get("username_key") != key:
            note_fail(key)
            h.send_json(400, {"error": "That password was not accepted."})
            return
        if accounts.totp_enabled(checked):
            _hooks["arm_factor"](key)
            set_pending(user, "totp")
            h.send_json(200, {"ok": True, "need": "totp"})
            return
        if not _verified_browser(h, user):
            _email_step(h, user, checked)
            return
        _grant(h, user)
        return
    if method == "POST" and sub == "verify/code":
        data = h.read_json_body() or {}
        stage = get_pending(user)
        typed = data.get("code") if isinstance(data.get("code"), str) else ""
        if locked_for(key):
            h.send_json(429, {"error": "Too many wrong tries. Wait a few minutes."})
            return
        if stage == "totp":
            full = accounts.find_user(key)
            if not full or not _hooks["take_factor"](full, typed):
                note_fail(key)
                h.send_json(400, {"error": "That code was not accepted."})
                return
            if not _verified_browser(h, user):
                _email_step(h, user, full)
                return
            _grant(h, user)
            return
        if stage == "email":
            if _hooks["take_email_code"](key, typed) is None:
                note_fail(key)
                h.send_json(400, {"error": "That code was not accepted."})
                return
            _grant(h, user)
            return
        h.send_json(400, {"error": "Start again with your password."})
        return
    if method == "POST" and sub == "verify/resend":
        h.read_json_body()
        if get_pending(user) != "email":
            h.send_json(400, {"error": "Start again with your password."})
            return
        _email_step(h, user, accounts.find_user(key) or user)
        return
    if method == "POST" and sub == "approval/start":
        h.read_json_body()
        rid, code = approval_start(user, h.headers.get("User-Agent"))
        h.send_json(200, {"ok": True, "id": rid, "code": code, "expires_in": APPROVAL_SECONDS})
        return
    if method == "POST" and sub == "approval/poll":
        data = h.read_json_body() or {}
        status = approval_poll(user, data.get("id"))
        if status == "approved":
            _grant(h, user, {"status": "approved"})
            return
        h.send_json(200, {"ok": True, "status": status})
        return
    if method == "POST" and sub == "approval/decide":
        data = h.read_json_body() or {}
        err = approval_decide(user, data.get("id"), data.get("approve") is True, _verified_browser(h, user))
        h.send_json(400 if err else 200, {"error": err} if err else {"ok": True})
        return
    if method == "POST" and sub == "lock":
        h.read_json_body()
        h.send_json_extra(200, {"ok": True}, [("Set-Cookie", clear_admin_cookie())])
        return

    # ---- everything below needs a live admin session
    if admin_left(h.headers.get("Cookie"), user) <= 0:
        h.send_json(403, {"error": "Confirm it's you to keep using the Administrator page.", "admin_required": True})
        return
    if method == "GET" and sub == "overview":
        body = overview(_hooks.get("version", ""))
        body["directory"] = directory_status()
        body["tools"] = sorted(_hooks.get("tools", {}).keys())
        h.send_json(200, body)
        return
    if method == "GET" and sub == "config":
        h.send_json(200, config_view())
        return
    if method == "POST" and sub == "config/apply":
        data = h.read_json_body() or {}
        result, status = apply(data.get("changes"), data.get("hash") or "", user["username"], accounts.find_user)
        if status == 200:
            _log("settings applied by %s: %s (backup %s)" % (user["username"], ", ".join(result["changed"]), result["backup"] or "none, new file"))
            result["restarting"] = True
            h.send_json(200, result)
            restart_soon(result["path"], result["backup"])
            return
        h.send_json(status, result)
        return
    if method == "POST" and sub == "directory/pause":
        data = h.read_json_body() or {}
        try:
            minutes = int(data.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = -1
        if minutes < 0 or minutes > 7 * 24 * 60:
            h.send_json(400, {"error": "Choose between 0 and 10080 minutes."})
            return
        set_directory_pause(minutes)
        h.send_json(200, {"ok": True, "directory": directory_status()})
        return
    # Account tools: the same server code as the Admin Tools in the profile menu.
    owner_map = {
        ("GET", "users"): "owner_users", ("POST", "users/remove"): "owner_remove", ("POST", "users/resend"): "owner_resend",
        ("POST", "users/invite"): "invite", ("POST", "users/role"): "owner_role", ("POST", "users/disable"): "owner_disable",
        # 26.10.2: cancel a pending invite; the same server check as everywhere else (the owner may cancel any).
        ("POST", "users/cancel-invite"): "cancel_invite",
        # 26.10.13.8: email a one-time password reset link that names the username.
        ("POST", "users/reset-email"): "owner_reset_email",
    }
    name = owner_map.get((method, sub))
    if name:
        getattr(h, name)(user)
        return
    # Extra tools this server line has (the public line adds reports and the language filter).
    tool = _hooks.get("tools", {}).get((method, sub))
    if tool:
        tool(h, user)
        return
    h.send_json(404, {"error": "That page was not found."})


def _email_step(h, user, full):
    """This browser was never verified: email a code, exactly like a sign-in on a new browser."""
    status = _hooks["email_code_for"](full)
    set_pending(user, "email")
    if status in ("ok", "wait"):
        h.send_json(200, {"ok": True, "need": "email", "note": "We emailed you a 6-digit code." if status == "ok" else "A code was already emailed. It expires in 10 minutes."})
    elif status == "soon":
        h.send_json(200, {"ok": True, "need": "email", "note": "Wait a minute before asking for another code."})
    else:
        h.send_json(200, {"ok": True, "need": "email", "note": "The code could not be emailed. Use approval from another device instead."})


def directory_status():
    """Is this server listed in the directory, and how did the last online note go?"""
    import directory
    try:
        with open(directory.STATUS_PATH, "r", encoding="utf-8") as fh:
            last = json.load(fh)
    except (OSError, ValueError):
        last = {}
    try:
        fp = directory.fingerprint()
    except Exception:
        fp = ""
    return {
        "reporting": bool(directory.REPORT),
        "home_mode": bool(directory.HOME_MODE),
        "home_url": directory.HOME_URL,
        "paused_until": directory.paused_until(),
        "last": {k: last.get(k) for k in ("at", "status", "state", "message")} if isinstance(last, dict) else {},
        "fingerprint": fp[:16] if isinstance(fp, str) else "",
        "name": directory.org_name(),
    }


def set_directory_pause(minutes):
    """Pause the online notes for N minutes (0 resumes). Same file manage.py directory-pause writes."""
    import directory
    if minutes <= 0:
        try:
            os.unlink(directory.PAUSE_PATH)
        except FileNotFoundError:
            pass
        return
    until = int(time.time()) + minutes * 60
    tmp = directory.PAUSE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(str(until))
    os.replace(tmp, directory.PAUSE_PATH)


def _log(text):
    print("admin page: %s" % text, flush=True)
