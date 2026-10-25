"""Settings for Branch Chat Server.

Every setting has a default. A real server should set at least BC_PUBLIC_URL,
BC_INSTANCE_NAME, BC_OWNER and BC_MAIL_FROM (the installer writes them).

Where a value comes from, highest priority first:
  1. the process environment (for example systemd Environment= or docker -e)
  2. the config file: $BC_CONFIG, or /etc/branch-chat/config.env when it exists
  3. the built-in default

The config file uses simple KEY=VALUE lines (the same format as a systemd
EnvironmentFile or a docker env_file). Lines starting with # are comments.
Values may be wrapped in single or double quotes. Nothing is executed.

Standard library only.
"""
import os
import re
from urllib.parse import urlsplit

DEFAULT_CONFIG_PATH = "/etc/branch-chat/config.env"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(path):
    """Read KEY=VALUE lines. Missing or unreadable files give an empty dict."""
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            # Double quotes: \" \\ and \$ are escapes (as in Docker Compose env files).
            value = re.sub(r'\\(["\\$])', r"\1", value[1:-1])
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        out[key] = value
    return out


# Which config file to read: BC_CONFIG, else the default path if it exists.
def _config_path():
    """Which settings file to read: $BC_CONFIG, else /etc/branch-chat/config.env, else (Docker) <data folder>/config.env.
    The last one is new in 0.23.3-beta.15: the Administrator page saves there in Docker installs, because the
    container cannot write the host's env_file. Values from the environment (env_file) still win over it."""
    path = os.environ.get("BC_CONFIG")
    if path:
        return path
    if os.path.isfile(DEFAULT_CONFIG_PATH):
        return DEFAULT_CONFIG_PATH
    state = os.environ.get("BC_STATE_DIR", "")
    if state and os.path.isfile(os.path.join(state, "config.env")):
        return os.path.join(state, "config.env")
    return ""


CONFIG_PATH = _config_path()
_FILE = parse_env_file(CONFIG_PATH) if CONFIG_PATH else {}


def get(key, default="", *aliases):
    """A string setting: environment, then config file, then default. Aliases are older names."""
    for name in (key,) + aliases:
        if name in os.environ:
            return os.environ[name]
    for name in (key,) + aliases:
        if name in _FILE:
            return _FILE[name]
    return default


# A whole-number setting; the default when empty or not a number.
def get_int(key, default):
    value = get(key, "")
    try:
        return int(value) if value.strip() else default
    except ValueError:
        return default


def get_secret(key, default=""):
    """A secret from KEY, or from the file named by KEY_FILE (Docker secrets style). Never logged."""
    value = get(key, "")
    if value:
        return value
    path = get(key + "_FILE", "")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return default
    return default


_HERE = os.path.dirname(os.path.abspath(__file__))

# --- Network ---------------------------------------------------------------
def _read_version():
    """The shared Branch Chat version (YY.MM.PATCH, e.g. 26.10.0). BC_VERSION wins; then a VERSION file next to
    this file (the Docker image and install.sh write one), then the repo-root VERSION (running from git)."""
    v = os.environ.get("BC_VERSION", "").strip()
    if v and v != "dev":
        return v
    for p in (os.path.join(_HERE, "VERSION"), os.path.join(os.path.dirname(_HERE), "VERSION")):
        try:
            with open(p, encoding="utf-8") as f:
                v = f.read().strip()
            if v and v != "dev":
                return v
        except OSError:
            pass
    return "dev"


VERSION = _read_version()
# Apps and server must share YY.MM. min_app is the oldest app this server works with.
API_LEVEL = ".".join(VERSION.split("-")[0].split(".")[:2]) if VERSION != "dev" else "dev"
MIN_APP = (API_LEVEL + ".0") if VERSION != "dev" else ""


HOST = get("BC_HOST", "127.0.0.1")
PORT = get_int("BC_PORT", 8766)

# --- Paths -----------------------------------------------------------------
STATE_DIR = get("BC_STATE_DIR", "/var/lib/huzz-chat")
APP_DIR = get("BC_APP_DIR", _HERE)

# --- Public address --------------------------------------------------------
DEFAULT_PUBLIC_URL = "http://localhost:8766"
PUBLIC_URL = (get("BC_PUBLIC_URL", DEFAULT_PUBLIC_URL).strip() or DEFAULT_PUBLIC_URL).rstrip("/")
PUBLIC_HOST = (urlsplit(PUBLIC_URL).hostname or "").lower()


# Host names this server answers to: its public host plus BC_HOSTS.
def _hosts():
    hosts = {PUBLIC_HOST} if PUBLIC_HOST else set()
    for item in get("BC_HOSTS", "").replace(" ", ",").split(","):
        item = item.strip().lower()
        if item:
            hosts.add(item)
    return hosts


HOSTS = _hosts()

# --- Instance / branding ---------------------------------------------------
# Empty means "no branding": pages are served byte-for-byte as they are on disk.
INSTANCE_NAME = get("BC_INSTANCE_NAME", "").strip()
# Optional personal credits for this one server (a JSON file with "about_html" and/or "icon_credit").
# The shared pages carry no personal names or addresses; a family server can add its own here. Empty = none.
CREDITS_FILE = get("BC_CREDITS_FILE", "").strip()
# When on (1), the About page is for signed-in members only: signed-out visitors are sent to sign-in, the same way
# the Administrator page works. Meant for a private family server whose About page carries personal credits.
# Off by default, so a public server (like branchchat.app) keeps its About page open to everyone.
ABOUT_REQUIRES_LOGIN = get("BC_ABOUT_REQUIRES_LOGIN", "").strip().lower() in ("1", "true", "yes", "on")
# Optional contact address for the About page's "Contact" link. Empty (the default) = no Contact link at all.
CONTACT_EMAIL = get("BC_CONTACT_EMAIL", "").strip()
ACCENT = get("BC_ACCENT", "").strip().lower()
BACKGROUND = get("BC_BACKGROUND", "").strip()
OWNER_USERNAME = (get("BC_OWNER", "", "BRANCH_OWNER").strip() or "admin").lower()

# --- Mail ------------------------------------------------------------------
MAIL_FROM = get("BC_MAIL_FROM", "BranchChat <noreply@localhost>")
# sendmail (default, as before) | smtp | log (codes are printed to the server log; for testing or LAN installs)
SMTP_HOST = get("BC_SMTP_HOST", "").strip()
MAIL_MODE = (get("BC_MAIL_MODE", "").strip().lower() or ("smtp" if SMTP_HOST else "sendmail"))
SENDMAIL = get("BC_SENDMAIL", "/usr/sbin/sendmail")
SMTP_PORT = get_int("BC_SMTP_PORT", 587)
SMTP_USER = get("BC_SMTP_USER", "")
SMTP_TLS = (get("BC_SMTP_TLS", "").strip().lower() or ("ssl" if SMTP_PORT == 465 else "starttls"))
INVITE_NOTE = get("BC_INVITE_NOTE", "").strip()


def smtp_password():
    """Read on demand so the password is not kept in a module global."""
    return get_secret("BC_SMTP_PASSWORD")


# True when BC_PUBLIC_URL was never set (still the localhost default).
def is_default_public_url():
    return PUBLIC_URL == DEFAULT_PUBLIC_URL


# True when this server sets its own name, accent colour or background (white-label branding).
def branding_enabled():
    return bool(INSTANCE_NAME or ACCENT or BACKGROUND)
