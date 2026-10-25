"""Download links on the sign-in page (branchchat.app). OFF unless BC_DOWNLOAD_LINKS=1.

With the flag off (the default, and the private server), nothing here runs:
login.html is served unchanged and /download/* is a normal 404.

Settings (environment or the BC_CONFIG env file):
  BC_DOWNLOAD_LINKS   1/true/yes/on shows the "Get BranchChat" card above the sign-in form
                      and serves files from BC_DOWNLOAD_DIR at /download/<file>
  BC_DOWNLOAD_DIR     folder with the files to offer (default: <BC_STATE_DIR>/downloads)
  BC_APK_FILE         APK file name in that folder (default: the newest BranchChat-*.apk there)
  BC_APK_BETA         label the APK as beta (default 1)
  BC_APPSTORE_URL     App Store listing. Empty = "App Store, coming soon"
  BC_PLAY_URL         Google Play listing. Empty = "Google Play, coming soon"
  BC_WINDOWS_URL      Windows installer, macOS build, Linux build. Each is an https:// URL
  BC_MACOS_URL        or a /download/<file> path. Empty = "coming soon" for that platform
  BC_LINUX_URL        and the browser is offered instead.
  Linux (public.6): picking the Linux tile opens a menu of package formats. Each is offered once its URL is set:
  BC_LINUX_DEB_URL, BC_LINUX_RPM_URL, BC_LINUX_APPIMAGE_URL, BC_LINUX_APPIMAGE_ARM64_URL, BC_LINUX_TAR_URL,
  BC_LINUX_TAR_ARM64_URL (and BC_LINUX_FLATPAK_URL: the Flatpak row only appears when that is set).
  BC_LINUX_TAR_SHA256 / BC_LINUX_TAR_ARM64_SHA256 give the checksum for an https:// tarball (a /download/ file is
  hashed here). The old single BC_LINUX_URL still works: it fills the row that matches its file type.
Every "coming soon" item is a real link to #signin (the sign-in form), so nothing is a dead end,
with or without JavaScript. URLs are read at start-up: restart the container after changing them.

Standard library only. Never imports server.py.
"""
import hashlib
import html
import os
import re
import threading

import bcconfig

_TRUE = ("1", "true", "yes", "on")
ENABLED = bcconfig.get("BC_DOWNLOAD_LINKS", "").strip().lower() in _TRUE
ASSET_V = "157"
STATE_DIR = bcconfig.get("BC_STATE_DIR", "/var/lib/huzz-chat")
DIR = bcconfig.get("BC_DOWNLOAD_DIR", "") or os.path.join(STATE_DIR, "downloads")
APK_FILE = bcconfig.get("BC_APK_FILE", "").strip()
APK_BETA = bcconfig.get("BC_APK_BETA", "1").strip().lower() in _TRUE

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
TYPES = {
    ".apk": "application/vnd.android.package-archive",
    ".aab": "application/octet-stream",
    ".exe": "application/vnd.microsoft.portable-executable",
    ".msi": "application/x-msi",
    ".dmg": "application/x-apple-diskimage",
    ".appimage": "application/vnd.appimage",
    ".deb": "application/vnd.debian.binary-package",
    ".rpm": "application/x-rpm",
    ".flatpak": "application/vnd.flatpak",
    ".zip": "application/zip",
    ".gz": "application/gzip",
}
_lock = threading.Lock()
EXTRA_HTML = None  # publicmode sets this to the "host your own server" link + pop-up (public server only)
_cache = {}  # path -> (mtime, size, sha256)


def _url(key):
    """A store or build URL: https://... or /download/<file>. Anything else is treated as unset."""
    value = bcconfig.get(key, "").strip()
    if value.startswith("https://") and not any(c in value for c in ' "<>\'`'):
        return value
    if value.startswith("/download/") and _NAME_RE.match(value[len("/download/"):]):
        return value
    return ""


APPSTORE_URL = _url("BC_APPSTORE_URL")
PLAY_URL = _url("BC_PLAY_URL")
DESKTOP_URLS = {"windows": _url("BC_WINDOWS_URL"), "macos": _url("BC_MACOS_URL"), "linux": _url("BC_LINUX_URL")}

# ---- Linux formats (public.6). Requirements come from the real 26.10.0 Linux build (Electron 44.8.0) and were
# checked by installing and starting that build in clean distro containers on 2026-10-17; see LINUX_REQS_NOTE.
LINUX_REQS_NOTE = ("From the 26.10.0 Linux build (Electron 44.8.0; its binaries need glibc 2.25 or newer) and from installing "
                   "and starting it in clean Debian, Ubuntu and Fedora containers on 17 Oct 2026. \u201cLowest tested\u201d is the "
                   "oldest release it was run on; older ones may work but weren't checked. arm64 builds exist but weren't tested.")
LINUX_VARIANTS = [
    {"id": "deb", "title": "Debian / Ubuntu", "short": ".deb", "fmt": ".deb package \u00b7 apt installs what it needs",
     "env": {"x86_64": "BC_LINUX_DEB_URL"}, "archs": ["x86_64"],
     "req": [("Lowest tested", "Ubuntu 20.04 LTS (glibc 2.31)"), ("Recommended", "Debian 12 or Ubuntu 22.04 LTS and newer"),
             ("Tested on", "Debian 12, Ubuntu 20.04, Ubuntu 22.04 (needs libgbm1, already there on desktop installs)")]},
    {"id": "rpm", "title": "Fedora / RHEL", "short": ".rpm", "fmt": ".rpm package \u00b7 dnf installs what it needs",
     "env": {"x86_64": "BC_LINUX_RPM_URL"}, "archs": ["x86_64"],
     "req": [("Lowest tested", "Fedora 40 (glibc 2.39)"), ("Recommended", "Fedora 40 or newer"),
             ("Tested on", "Fedora 40 (RHEL, Rocky and Alma not tested yet)")]},
    {"id": "appimage", "title": "AppImage", "short": "AppImage", "fmt": "any distro \u00b7 one file, no install",
     "env": {"x86_64": "BC_LINUX_APPIMAGE_URL", "arm64": "BC_LINUX_APPIMAGE_ARM64_URL"}, "archs": ["x86_64", "arm64"],
     "req": [("Lowest tested", "Ubuntu 20.04 LTS (glibc 2.31)"), ("Recommended", "Debian 12 or Ubuntu 22.04 LTS and newer"),
             ("Needs", "a desktop with GTK 3 and NSS; libfuse2 to run it directly (or start it with --appimage-extract-and-run)"),
             ("Tested on", "Debian 12, Ubuntu 20.04 (x86_64)")]},
    {"id": "flatpak", "title": "Flatpak", "short": "Flatpak", "fmt": "sandboxed, any distro with Flatpak", "only_if_set": True,
     "env": {"x86_64": "BC_LINUX_FLATPAK_URL"}, "archs": ["x86_64"],
     "req": [("Needs", "Flatpak with the Flathub remote (pulls the Electron base app)")]},
    {"id": "tar", "title": "tar.gz", "short": "tar.gz", "fmt": "self-install, no root needed", "steps": True,
     "env": {"x86_64": "BC_LINUX_TAR_URL", "arm64": "BC_LINUX_TAR_ARM64_URL"}, "archs": ["x86_64", "arm64"],
     "req": [("Lowest tested", "Ubuntu 20.04 LTS (glibc 2.31)"), ("Recommended", "Debian 12 or Ubuntu 22.04 LTS and newer"),
             ("Needs", "the same desktop libraries as the .deb: GTK 3, NSS, libgbm1, ALSA"),
             ("Tested on", "Debian 12, Ubuntu 20.04 (x86_64)")]},
]


def _linux_urls():
    """{variant id: {arch: url}} from the BC_LINUX_* settings (plus the legacy BC_LINUX_URL by file type)."""
    out = {}
    for v in LINUX_VARIANTS:
        for arch, key in v["env"].items():
            u = _url(key)
            if u:
                out.setdefault(v["id"], {})[arch] = u
    legacy = DESKTOP_URLS["linux"]
    if legacy:
        low = legacy.lower()
        kind = ("deb" if low.endswith(".deb") else "rpm" if low.endswith(".rpm") else "tar" if low.endswith(".tar.gz")
                else "flatpak" if low.endswith(".flatpak") else "appimage")
        arch = "arm64" if re.search(r"(arm64|aarch64)", low) else "x86_64"
        out.setdefault(kind, {}).setdefault(arch, legacy)
    return out


# 26.10.3: with no BC_LINUX_* links set, the public home points at the app files on the public GitHub releases page
# (BC_APP_RELEASES_URL; "off" turns this off). Since 26.10.10 the links name the files of the actual Latest release
# (see latest_tag below), so they work whatever version this server runs.
_REL = bcconfig.get("BC_APP_RELEASES_URL", "https://github.com/NexusMcNexis/branch-chat-releases").strip().rstrip("/")
APP_RELEASES = _REL if (ENABLED and _REL.startswith("https://") and not any(c in _REL for c in ' "<>\'`')) else ""
_VER = getattr(bcconfig, "VERSION", "") or ""
# 26.10.9.1: a server-only hotfix adds a 4th number (YY.MM.PATCH.HOTFIX). The apps are not rebuilt for it, so
# app file names keep the first three numbers (26.10.9.1 -> BranchChat-26.10.9.apk).
_m = re.match(r"^([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+$", _VER)
if _m:
    _VER = _m.group(1)
_GH_FILES = {"deb": {"x86_64": "-amd64.deb"}, "rpm": {"x86_64": "-x86_64.rpm"},
             "appimage": {"x86_64": "-x86_64.AppImage", "arm64": "-arm64.AppImage"},
             "tar": {"x86_64": "-x64.tar.gz", "arm64": "-arm64.tar.gz"}}


# 26.10.10: the file names come from the release that is actually "Latest" on the releases page, never from this
# server's own version (a server can run ahead of, or behind, the published apps; any self-hosted server works).
# The tag is read from the GitHub API (releases/latest), cached LATEST_TTL seconds and refreshed in the background.
# If the API can't be reached, the last known-good tag (kept in memory and in BC_STATE_DIR) is used, pinned with
# releases/download/<tag>/ so the link always names a file that exists; with no known tag at all, this server's
# version is the last resort.
import json
import time
import urllib.request

LATEST_TTL = 600
_TAG_RE = r"^v?[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$"   # 26.10.13.13: 4-part tags too
_LATEST_FILE = os.path.join(STATE_DIR, "downloads-latest.json")
_latest = {"tag": "", "at": 0.0, "busy": False, "assets": None}  # assets: file names on that release


def _api_url():
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", APP_RELEASES)
    return "https://api.github.com/repos/%s/releases/latest" % m.group(1) if m else ""


def _fetch_latest_tag():
    url = _api_url()
    if not url:
        return "", None
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "BranchChat-server"})
    with urllib.request.urlopen(req, timeout=4) as r:
        d = json.load(r)
    tag = str(d.get("tag_name", ""))
    if not re.match(_TAG_RE, tag):
        return "", None
    return tag, sorted(str(x.get("name", "")) for x in d.get("assets", []) if isinstance(x, dict))


def _load_known():
    try:
        with open(_LATEST_FILE) as fh:
            d = json.load(fh)
        tag = str(d.get("tag", ""))
        assets = d.get("assets") if isinstance(d.get("assets"), list) else None
        return (tag, assets) if re.match(_TAG_RE, tag) else ("", None)
    except (OSError, ValueError, AttributeError):
        return "", None


def _refresh():
    try:
        tag, assets = _fetch_latest_tag()
    except Exception:
        tag, assets = "", None
    with _lock:
        _latest["busy"] = False
        _latest["at"] = time.time()   # failures also wait a TTL before the next try
        if tag:
            changed = tag != _latest["tag"] or assets != _latest["assets"]
            _latest["tag"] = tag
            _latest["assets"] = assets
        else:
            changed = False
    if tag and changed:
        try:
            tmp = _LATEST_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"tag": tag, "assets": assets}, fh)
            os.replace(tmp, _LATEST_FILE)
        except OSError:
            pass


def latest_tag():
    """The Latest release tag (e.g. "v26.10.9"), or "" when unknown."""
    if not APP_RELEASES:
        return ""
    with _lock:
        if not _latest["tag"] and not _latest["at"]:
            _latest["tag"], _latest["assets"] = _load_known()
        first = not _latest["at"]
        stale = time.time() - _latest["at"] > LATEST_TTL
        start = stale and not _latest["busy"]
        if start:
            _latest["busy"] = True
    if start:
        if first and not _latest["tag"]:
            _refresh()           # nothing known yet: wait for the answer once (4 s timeout)
        else:
            threading.Thread(target=_refresh, daemon=True).start()
    return _latest["tag"]


def release_version():
    """The app version the download links offer (from the Latest tag; this server's version as a last resort)."""
    tag = latest_tag()
    if tag:
        return tag.lstrip("v")
    return _VER if re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", _VER) else ""


# One published app file on the releases page, e.g. .../releases/download/v26.10.9/BranchChat-26.10.9-amd64.deb ("" when off).
def release_file_url(suffix):
    if not APP_RELEASES:
        return ""
    tag = latest_tag() or (("v" + _VER) if re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", _VER) else "")
    if not tag:
        return ""
    name = "BranchChat-%s%s" % (tag.lstrip("v"), suffix)
    assets = _latest.get("assets")
    if assets is not None and name not in assets:
        return ""   # 26.10.13.13: only link files that are really on the Latest release (no dead .deb/.rpm links)
    return "%s/releases/download/%s/%s" % (APP_RELEASES, tag, name)


_WIN_ENV = bool(DESKTOP_URLS["windows"])


def windows_url():
    """BC_WINDOWS_URL, else the Windows installer (.exe) on the Latest release."""
    return DESKTOP_URLS["windows"] if _WIN_ENV else release_file_url("-x64.exe")


LINUX_URLS = _linux_urls()
_GH_LINUX = not LINUX_URLS and bool(APP_RELEASES)   # Linux links follow the Latest release (recomputed per page)
if LINUX_URLS or _GH_LINUX:
    DESKTOP_URLS["linux"] = "#bc-linux"   # the Linux tile is available and opens the menu


def _linux_now():
    if _GH_LINUX:
        out = {k: {a: u for a, sfx in arches.items() for u in [release_file_url(sfx)] if u} for k, arches in _GH_FILES.items()}
        return {k: v for k, v in out.items() if v}   # 26.10.13.13: formats not on the Latest release show "coming soon"
    return LINUX_URLS


def _file_info(name):
    """(size, sha256) for a file in DIR, cached by mtime. None when missing or not allowed."""
    if not name or not _NAME_RE.match(name) or os.path.splitext(name)[1].lower() not in TYPES:
        return None
    path = os.path.join(DIR, name)
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[1], hit[2]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    with _lock:
        _cache[path] = (st.st_mtime_ns, st.st_size, h.hexdigest())
    return st.st_size, h.hexdigest()


# The Android app file to offer: BC_APK_FILE, else the newest BranchChat-*.apk in the downloads folder.
def _apk_name():
    if APK_FILE:
        return APK_FILE
    try:
        names = [n for n in os.listdir(DIR) if re.match(r"^BranchChat-[0-9][A-Za-z0-9._-]*\.apk$", n)]
    except OSError:
        return ""
    names.sort(key=lambda n: os.path.getmtime(os.path.join(DIR, n)), reverse=True)
    return names[0] if names else ""


def apk():
    """{'name','url','version','size','sha256'} or None."""
    name = _apk_name()
    info = _file_info(name)
    if not info or not name.lower().endswith(".apk"):
        # 26.10.3: no APK in the downloads folder -> offer the one on the public releases page (size/checksum are
        # listed there, so they are left out here).
        url = release_file_url(".apk")
        if url:
            ver = release_version()
            return {"name": "BranchChat-%s.apk" % ver, "url": url, "version": ver, "size": 0, "sha256": ""}
        return None
    m = re.match(r"^BranchChat-([0-9][A-Za-z0-9._-]*?)\.apk$", name)
    return {"name": name, "url": "/download/" + name, "version": m.group(1) if m else "", "size": info[0], "sha256": info[1]}


# File size in friendly units (KB or MB).
def _size(n):
    if n >= 1 << 20:
        return "%.1f MB" % (n / float(1 << 20))
    return "%d KB" % max(1, round(n / 1024.0))


# Escape text for HTML.
def _e(text):
    return html.escape(str(text), quote=True)


PLATFORM_NAMES = {"ios": "iPhone and iPad", "android": "Android", "windows": "Windows", "macos": "Mac", "linux": "Linux"}


SPRITE = '<svg class="bc-dl-sprite" width="0" height="0" aria-hidden="true" focusable="false">\n<symbol id="i-android" viewBox="0 0 24 24"><path fill="currentColor" d="M7.2 4.1 6 2.2l.8-.5L8 3.7A8.6 8.6 0 0 1 12 2.8c1.4 0 2.8.3 4 .9l1.2-2 .8.5-1.2 1.9A6.9 6.9 0 0 1 19.5 10h-15a6.9 6.9 0 0 1 2.7-5.9ZM9 7.4a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8Zm6 0a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8ZM4.5 11h15v7.5a1.5 1.5 0 0 1-1.5 1.5h-1v2.5a1.5 1.5 0 0 1-3 0V20h-4v2.5a1.5 1.5 0 0 1-3 0V20H6a1.5 1.5 0 0 1-1.5-1.5V11Z"/></symbol>\n<symbol id="i-play" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M5 3.5v17l15-8.5L5 3.5Z"/></symbol>\n<symbol id="i-phone" viewBox="0 0 24 24"><rect x="6" y="2" width="12" height="20" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M10.5 18.5h3"/></symbol>\n<symbol id="i-monitor" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.8"/><path stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M9 20h6M12 16v4"/></symbol>\n<symbol id="i-laptop" viewBox="0 0 24 24"><rect x="4.5" y="4.5" width="15" height="10.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.8"/><path stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M2.5 19h19"/></symbol>\n<symbol id="i-linux" viewBox="0 0 24 24"><rect x="2.5" y="4" width="19" height="16" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="m6.5 9 3 3-3 3M12 15h5"/></symbol>\n<symbol id="i-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/><path fill="none" stroke="currentColor" stroke-width="1.6" d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3Z"/></symbol>\n<symbol id="i-down" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M12 3.5v12m-5-5 5 5 5-5M4.5 20h15"/></symbol>\n</svg>'


def _items(a):
    """The platform tiles in their original order, plus the always-available browser tile (sorted later)."""
    soon = "Coming soon"
    out = []
    if a:
        sub = "APK beta \u00b7 v%s" % a["version"] if a["version"] else "APK beta"
        out.append({"id": "apk", "os": "android", "icon": "i-android", "title": "Android", "sub": sub, "url": a["url"],
                    "pill": "Beta" if APK_BETA else "", "download": a["name"],
                    "label": ("Android (APK, beta), version %s, %s" % (a["version"], _size(a["size"]))) if a["size"] else
                             "Android (APK), version %s" % a["version"]})
    else:
        out.append({"id": "apk", "os": "android", "icon": "i-android", "title": "Android", "sub": soon, "url": "",
                    "note": "The Android download is coming soon. Sign in here in your browser for now."})
    out.append({"id": "play", "os": "android", "icon": "i-play", "title": "Google Play", "sub": "Android" if PLAY_URL else soon, "url": PLAY_URL,
                "note": "The Google Play app is coming soon. Sign in here in your browser for now."})
    out.append({"id": "appstore", "os": "ios", "icon": "i-phone", "title": "App Store", "sub": "iPhone and iPad" if APPSTORE_URL else "iPhone \u00b7 coming soon",
                "url": APPSTORE_URL, "note": "The App Store app is coming soon. Sign in here in your browser for now."})
    for key, title, icon in (("windows", "Windows", "i-monitor"), ("macos", "macOS", "i-laptop"), ("linux", "Linux", "i-linux")):
        url = windows_url() if key == "windows" else DESKTOP_URLS[key]
        out.append({"id": key, "os": key, "icon": icon, "title": title, "sub": "Desktop app" if url else soon, "url": url,
                    "note": "The %s app is coming soon. Sign in here in your browser for now." % title,
                    "menu": key == "linux"})
    # public.5: using it in the browser is always a choice of its own, and always available.
    out.append({"id": "web", "os": "web", "icon": "i-globe", "title": "Use in your browser", "sub": "Works now \u00b7 no install",
                "url": "#signin", "browser": True, "note": "Sign in here to use BranchChat in your browser."})
    return out


DOWNLOAD_TITLES = {"play": "Download from Google Play", "appstore": "Download from the App Store"}


# One download tile (icon, title, note, link or menu) on the Get the app page.
def _tile(it):
    icon = '<svg aria-hidden="true"><use href="#%s"/></svg>' % it["icon"]
    pill = ' <span class="pill">%s</span>' % _e(it["pill"]) if it.get("pill") else ""
    title = it["title"]
    if it["url"] and not it.get("browser"):
        title = DOWNLOAD_TITLES.get(it["id"], "Download " + it["title"])   # e.g. "Download Linux"
    text = '<span><b>%s%s</b><small>%s</small></span>' % (_e(title), pill, _e(it["sub"]))
    if it.get("menu"):   # public.6: the Linux tile opens the format menu (#bc-linux), available or not
        cls = "plat" if it["url"] else "plat soon"
        return ('<li><a class="%s" data-dl="linux" data-os="linux" href="#bc-linux" aria-haspopup="dialog" aria-controls="bc-linux"%s>%s%s</a></li>'
                % (cls, "" if it["url"] else ' data-dl-note="%s"' % _e(it["note"]), icon, text))
    if it.get("browser"):
        return ('<li><a class="plat web" data-dl="web" data-os="web" href="#signin" data-dl-note="%s">%s%s</a></li>'
                % (_e(it["note"]), icon, text))
    if it["url"]:
        extra = ' download="%s"' % _e(it["download"]) if it.get("download") else ""
        if it["url"].startswith("https://"):
            extra += ' rel="noopener" target="_blank"'
        if it.get("label"):
            extra += ' aria-label="%s"' % _e(it["label"])
        return '<li><a class="plat" data-dl="%s" data-os="%s" href="%s"%s>%s%s</a></li>' % (it["id"], it["os"], _e(it["url"]), extra, icon, text)
    return ('<li><a class="plat soon" data-dl="%s" data-os="%s" href="#signin" data-dl-note="%s" aria-label="%s, coming soon. Sign in on the web for now.">%s%s</a></li>'
            % (it["id"], it["os"], _e(it["note"]), _e(it["title"]), icon, text))


# The SHA-256 to show for a download: computed for local files, from the config for outside links.
def _sha_for(url, env_key):
    if url.startswith("/download/"):
        info = _file_info(url[len("/download/"):])
        return info[1] if info else ""
    v = bcconfig.get(env_key, "").strip().lower()
    return v if re.match(r"^[0-9a-f]{64}$", v) else ""


# Make a download link absolute (for copy-paste terminal commands).
def _abs(url):
    base = (bcconfig.get("BC_PUBLIC_URL", "") or "https://branchchat.app").rstrip("/")
    return url if url.startswith("https://") else base + url


# A command box with a Copy button.
def _code(cmd, label):
    return ('<div class="lx-code"><pre><code>%s</code></pre><button type="button" class="lx-copy" aria-label="Copy: %s">Copy</button></div>'
            % (_e(cmd), _e(label)))


# Linux .tar.gz install steps (download, check, extract, run) as command boxes.
def _tar_steps(url, arch):
    name = url.rsplit("/", 1)[-1] or "BranchChat-linux.tar.gz"
    folder = name[:-len(".tar.gz")] if name.endswith(".tar.gz") else "BranchChat"
    sha = _sha_for(url, "BC_LINUX_TAR_ARM64_SHA256" if arch == "arm64" else "BC_LINUX_TAR_SHA256")
    steps = [("Download", "curl -fLO %s" % _abs(url))]
    if sha:
        steps.append(("Check the download (prints \u201cOK\u201d)", 'echo "%s  %s" | sha256sum -c -' % (sha, name)))
    steps.append(("Extract to ~/.local/opt", "mkdir -p ~/.local/opt && tar -xzf %s -C ~/.local/opt" % name))
    steps.append(("Run", "~/.local/opt/%s/branch-chat" % folder))
    steps.append(("Optional: add it to your app menu", (
        "mkdir -p ~/.local/share/applications && cat > ~/.local/share/applications/branch-chat.desktop <<EOF\n"
        "[Desktop Entry]\nType=Application\nName=BranchChat\nExec=$HOME/.local/opt/%s/branch-chat %%U\n"
        "Categories=Network;Chat;\nTerminal=false\nEOF" % folder)))
    out = ['<ol class="lx-steps">']
    for title, cmd in steps:
        out.append('<li><span>%s</span>%s</li>' % (_e(title), _code(cmd.replace("\\n", "\n"), title)))
    out.append("</ol>")
    if not sha:
        out.append('<p class="lx-fine">No checksum is published for this file yet, so the check step is left out.</p>')
    return "".join(out)


def linux_panel():
    """The Linux format menu: a bottom sheet on phones, an inline panel on desktop (downloads.js opens it).
    Ready formats first, "coming soon" after (stable order); "Use in your browser" stays as the last choice."""
    urls = _linux_now()
    rows = [v for v in LINUX_VARIANTS if not v.get("only_if_set") or v["id"] in urls]
    rows = sorted(rows, key=lambda v: 0 if urls.get(v["id"]) else 1)
    parts = []
    for v in rows:
        have = urls.get(v["id"], {})
        head = '<div class="lx-head"><h3>%s</h3><small>%s</small>%s</div>' % (
            _e(v["title"]), _e(v["fmt"]), "" if have else '<span class="lx-soon">Coming soon</span>')
        body = []
        if have:
            btns = []
            for arch in v["archs"]:
                if arch in have:
                    u = have[arch]
                    extra = ' rel="noopener" target="_blank"' if u.startswith("https://") else ' download'
                    btns.append('<a class="lx-dl" href="%s"%s>Download %s <span>(%s)</span></a>' % (_e(u), extra, _e(v["short"]), _e(arch)))
            body.append('<div class="lx-btns">%s</div>' % "".join(btns))
        req = ['<dl class="lx-req">']
        for label, value in v["req"]:
            req.append("<dt>%s</dt><dd>%s</dd>" % (_e(label), _e(value)))
        req.append("<dt>Architecture</dt><dd>%s</dd>" % _e(" \u00b7 ".join(v["archs"])))
        req.append("</dl>")
        body.append("".join(req))
        if v.get("steps"):
            if have:
                for arch in v["archs"]:
                    if arch in have:
                        body.append('<details class="lx-how"%s><summary>Install steps (%s)</summary>%s</details>' % (
                            " open" if arch == "x86_64" else "", _e(arch), _tar_steps(have[arch], arch)))
            else:
                body.append('<p class="lx-fine">Install steps (download, check, extract, run) appear here when the file is published.</p>')
        parts.append('<li class="lx-opt%s" data-lx="%s">%s%s</li>' % ("" if have else " soon", v["id"], head, "".join(body)))
    # 26.10.13.2: Bazzite (and other Fedora Atomic desktops) ship Gear Lever for AppImages, so a short
    # Gear Lever walkthrough sits right under the format list. It reuses the AppImage download link.
    ai = urls.get("appimage", {}).get("x86_64", "")
    ai_btn = (' <a class="lx-dl" href="%s"%s>Download AppImage</a>' % (_e(ai), ' rel="noopener" target="_blank"' if ai.startswith("https://") else " download")) if ai else ""
    bazzite = ('<li class="lx-opt" id="bc-bazzite"><div class="lx-head"><h3>Bazzite</h3><small>AppImage with Gear Lever</small></div>'
               '<ol class="lx-steps"><li>Download the BranchChat AppImage.%s</li>'
               '<li>Right-click it, then <b>Open With</b> \u2192 <b>Gear Lever</b> (or open Gear Lever and drag the file in).</li>'
               '<li>Click <b>Unlock</b>, then <b>Move to the app drawer</b>. BranchChat then shows up in your app menu, and Gear Lever can update it.</li></ol>'
               '<p class="lx-fine">No Gear Lever? <a href="https://flathub.org/apps/it.mijorus.gearlever" rel="noopener" target="_blank">Install it from Flathub</a>. '
               'For Game Mode, add BranchChat in Steam with <b>Add a Non-Steam Game</b>.</p></li>') % ai_btn
    parts.append(bazzite)
    parts.append('<li class="lx-opt lx-web"><a href="#signin" data-dl-note="Sign in here to use BranchChat in your browser.">'
                 '<svg aria-hidden="true"><use href="#i-globe"/></svg><span><b>Use in your browser</b>'
                 '<small>Works now \u00b7 no install</small></span></a></li>')
    return ('<div class="lx-backdrop" hidden></div>\n'
            '<div id="bc-linux" class="lx" role="dialog" aria-modal="true" aria-labelledby="lx-title" hidden>\n'
            '  <div class="lx-top"><h3 id="lx-title">BranchChat for Linux</h3>'
            '<button type="button" class="lx-close" aria-label="Close">\u00d7</button></div>\n'
            '  <p class="lx-lead">Pick the package for your distribution.</p>\n'
            '  <ul class="lx-list">%s</ul>\n  <p class="lx-fine lx-note">%s</p>\n</div>' % ("".join(parts), _e(LINUX_REQS_NOTE)))


HERO = ('    <p class="eyebrow">Private chat for family and friends</p>\n'
        '    <h2 id="dl-title">Get <span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span></h2>\n'
        '    <p class="lead">Use the app on your phone or computer, or sign in right here in your browser.</p>\n')


# The whole downloads section: ready platforms first, then "coming soon".
def section(hero=True):
    a = apk()
    # v0.22.8-public.5: ready first, "coming soon" last, from each item's own availability (a working URL);
    # sorted() is stable, so the original order is kept inside each group. downloads.js then moves the visitor's
    # own platform to the front when that one is available.
    # 26.10.3 (tester feedback): only tiles you can use are shown; platforms that aren't ready yet share one quiet line
    # under the list instead of a row of "Coming soon" tiles.
    items = _items(a)
    later = [it["title"] for it in items if not it["url"] and it["id"] != "play"]
    tiles = "\n      ".join(_tile(it) for it in items if it["url"])
    soon_line = ('<p class="dl-later">%s: on the way. Until then, BranchChat works in your browser on any device.</p>'
                 % _e(", ".join(later).replace("App Store", "iPhone and iPad"))) if later else ""
    sha = ""
    if a and a["sha256"]:
        sha = ('<p class="dl-sha">Android APK %s \u00b7 %s \u00b7 beta, not yet tested on a real phone \u00b7 SHA-256 <code>%s</code></p>'
               % (_e(a["version"]), _size(a["size"]), _e(a["sha256"])))
    # Server-side the smart button is the generic one; downloads.js swaps in the visitor's platform.
    return """%s
<section id="bc-dl" class="dl" aria-labelledby="dl-title">
%s    <a id="bc-dl-main" class="dl-primary" href="#bc-dl-list">
      <svg id="dl-icon" aria-hidden="true"><use href="#i-down"/></svg>
      <span><span id="dl-label">Choose your platform</span><span class="sub" id="dl-sub">Phone, tablet or computer</span></span>
    </a>
    <p class="dl-meta">Not your device? Pick another below.</p>
    <ul id="bc-dl-list" class="plats" aria-label="All platforms">
      %s
    </ul>
    %s
    %s
    %s<p class="login-jump">or log in here <a class="ghost-btn" id="login-jump" href="#signin" data-dl-note="">Log in</a></p>
    <p class="dl-web">BranchChat works in any modern browser on Windows, Mac and Linux: <a href="#signin" data-dl-note="Sign in here to use BranchChat in your browser.">use it in your browser</a>. On a phone, choose <b>Add to Home Screen</b> in the browser menu for an app icon.</p>
    %s
    %s
  </section>
""" % (SPRITE, HERO if hero else '<h2 id="dl-title" class="fd-dl-title">Download BranchChat</h2>\n', tiles, soon_line, "", linux_panel(), sha, EXTRA_HTML() if callable(EXTRA_HTML) else "")


def page(name, body, get_page=False):
    """Wrap the sign-in form: download section on the left (stacked above below 960px), sign-in on the right.
    Other pages pass through. Only called when ENABLED."""
    if name != "login.html":
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return body
    start = text.find('<form id="login">')
    end = text.find("</form>", start)
    if start < 0 or end < 0:
        return body
    end += len("</form>")
    form = text[start:end].replace('<form id="login">', '<form id="login">\n  <p id="bc-dl-note" class="soon-note" role="status" hidden></p>', 1)
    wrapped = ('<main class="home" id="bc-home">\n' + section() + '  <div id="signin" class="signin-wrap" tabindex="-1">\n' + form + "\n  </div>\n</main>")
    text = text[:start] + wrapped + text[end:]
    head = '<link rel="stylesheet" href="/static/downloads.css?v=%s">' % ASSET_V
    if "/static/scheme.js" not in text:
        head = '<script src="/static/scheme.js?v=2"></script>\n' + head
    # Mockup v3: the /get page is the download column only (no sign-in column; "use it in your browser" goes to the
    # front door's Log in), with the Linux formats shown open. The sign-in form stays in the page, hidden, for login.js.
    if get_page:
        text = text.replace('<div id="signin" class="signin-wrap" tabindex="-1">', '<div id="signin" class="signin-wrap" tabindex="-1" hidden>', 1)
    text = text.replace('<html lang="en"', '<html lang="en" data-bc-dl="1"' + (' data-bc-get="1"' if get_page else ""), 1)
    text = text.replace("</head>", head + "\n</head>", 1)
    text = text.replace("</body>", '<script type="module" src="/static/downloads.js?v=%s"></script>\n</body>' % ASSET_V, 1)
    return text.encode("utf-8")


def route(h, method, path):
    """GET /download/<file>, before the session wall. True when answered."""
    if method != "GET" or not path.startswith("/download/"):
        return False
    name = path[len("/download/"):]
    info = _file_info(name)
    if not info:
        h.send_json(404, {"error": "That download was not found."})
        return True
    ctype = TYPES[os.path.splitext(name)[1].lower()]
    headers = [
        ("Content-Disposition", 'attachment; filename="%s"' % name),
        ("Cache-Control", "public, max-age=300"),
        ("X-Checksum-SHA256", info[1]),
    ]
    with open(os.path.join(DIR, name), "rb") as fh:
        body = fh.read()
    h.send_bytes(200, body, ctype, headers)
    return True
