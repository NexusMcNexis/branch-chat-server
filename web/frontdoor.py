"""Front door: the one-screen first page of branchchat.app (Oct 18 2026, approved 12:02 PM PT).

Replaces the "download tiles first" home page with the approved sign-up / log-in screen:
  - Create account (username first; email, password, repeat and the 13+ box on step 2, same screen)
  - Log in ("Already have an account? Log in"), Forgot password, authenticator and email-code steps (login.js, unchanged)
  - "Server: <this server> / Use my own server": type a server address, it is checked, then the hand-off pop-up
    ("Welcome to BranchChat. You're being redirected to <org>") sends you to that server's own sign-in.
  - Tiny footer: version · Host your own / Get the server app (· Support BranchChat, browsers only)
Inside the apps (User-Agent has "BranchChatApp/" or "BranchChatDesktop/", or the page sees window.BranchChatApp) there
are no download links at all. In a browser there is one small "Get the app" link to /get, which keeps the old
download page (tiles + the plain sign-in card) exactly as before.

OFF unless BC_FRONTDOOR=1 (default: on when BC_OPEN_SIGNUP / PUBLIC_MODE is on AND BC_HOME_MODE is on, i.e. the
public home only). Needs BC_OPEN_SIGNUP for "Create account" to work. The private server never runs this.
"?view=app" previews the app version in a normal browser (layout only; nothing else changes).

Standard library only. Never imports server.py.
"""
import html
import re
import urllib.parse

import bcconfig
import directory
import downloads
import publicmode

_TRUE = ("1", "true", "yes", "on")
_raw = bcconfig.get("BC_FRONTDOOR", "").strip().lower()
ENABLED = (_raw in _TRUE) if _raw else bool(publicmode.OPEN_SIGNUP and directory.HOME_MODE)
ASSET_V = "14"  # mobile tidy; 26.10.6: SW registration + Install app button; mockup: /get download flow, columns flipped
APP_RE = re.compile(r"\b(BranchChatApp|BranchChatDesktop)/[0-9A-Za-z._-]+")


def app_kind(user_agent, query=None):
    """'android' / 'desktop' when the request comes from one of the apps, else ''."""
    if query and (query.get("view") or [""])[0] == "app":
        return "android"
    m = APP_RE.search(user_agent or "")
    if not m:
        return ""
    return "android" if m.group(1) == "BranchChatApp" else "desktop"


# Escape text for HTML.
def _e(text):
    return html.escape(str(text), quote=True)


# The server's host name shown on the front door (from BC_PUBLIC_URL).
def _server_label():
    u = urllib.parse.urlsplit(bcconfig.PUBLIC_URL or "https://branchchat.app")
    return u.netloc or "branchchat.app"


def _cut(text, start_marker):
    """Remove and return the <form ...>...</form> that starts with start_marker."""
    start = text.find(start_marker)
    if start < 0:
        return text, ""
    end = text.find("</form>", start)
    if end < 0:
        return text, ""
    end += len("</form>")
    return text[:start] + text[end:], text[start:end]


ICO_INVITE = ('<svg viewBox="0 0 24 24" focusable="false"><rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M3.5 7l8.5 6 8.5-6"/></svg>')
ICO_FIND = ('<svg viewBox="0 0 24 24" focusable="false"><circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5"/></svg>')
ICO_PUBLIC = ('<svg viewBox="0 0 24 24" focusable="false"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5S9.4 5.9 12 3.5z"/></svg>')
SERVER_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3.5" y="4" width="17" height="6.5" rx="1.6"/>'
               '<rect x="3.5" y="13.5" width="17" height="6.5" rx="1.6"/><path d="M7 7.25h.01M7 16.75h.01"/></svg>')


# kofi-topbar (Oct 21 2026): visible Ko-fi pill right under the Browse groups / Try the public Lobby row. Browsers only (hidden in the apps by CSS,
# same rule as the footer .fd-donate link).
KOFI_HERO = ('<p class="fd-kofi-hero fd-donate"><a href="https://ko-fi.com/branchchat" id="fd-kofi-hero" target="_blank" '
             'rel="noopener noreferrer" aria-label="Support BranchChat on Ko-fi (opens in a new tab)">'
             'Support BranchChat.app <span aria-hidden="true">&#10084;&#65039;</span></a></p>')


# directory-signin (Oct 21 2026): the Browse groups pop-up "Sign in here" opens (browsers; fd-browse.js fills it).
FD_BROWSE = """<dialog class="fd-browse" id="fd-browse" aria-labelledby="fdb-title">
  <div class="fdb-head"><h2 id="fdb-title">Where do you sign in?</h2><button type="button" class="fdb-x" id="fdb-close" aria-label="Close">&times;</button></div>
  <p class="fdb-lead">Pick your group or server. Choose the Public server if you made your account on __SERVER__.</p>
  <input id="fdb-search" type="search" placeholder="Search groups" aria-label="Search groups" autocomplete="off">
  <ul class="fdb-list" id="fdb-list"></ul>
  <p class="fdb-alt"><button type="button" class="linkish" id="fdb-own">My group isn&rsquo;t listed: use its address</button></p>
</dialog>
"""


# The front door page body: logo, the three choices, the login and sign-up forms.
def _main(version, app, later_forms):
    server = _server_label()
    return """<main class="fd" id="bc-frontdoor">
  <header class="fd-hero">
    <span class="bc-icon"><img src="/favicon.png" alt="" width="72" height="72"></span>
    <h1><span class="bc-logo"><b>Branch</b><i>Chat</i><sup class="tm" aria-label="trademark">TM</sup></span></h1>
    <p class="fd-line">Private chat for family and friends.</p>
  </header>

  <div class="fd-gate" id="fd-gate">
    <button id="gate-create" type="button">Create account</button>
    <button id="gate-signin" class="ghost" type="button" aria-expanded="false" aria-controls="login">__SIGNIN_LABEL__</button>
  </div>
  <form id="fd-pick" hidden novalidate>
    <p class="fd-title">Which server are you joining?</p>
    <label class="su-check"><input type="radio" name="srv" value="none" checked> <span>I don&rsquo;t have a server <small>(free account on branchchat.app)</small></span></label>
    <label class="su-check"><input type="radio" name="srv" value="public"> <span>branchchat.app</span></label>
    <label class="su-check"><input type="radio" name="srv" value="woods"> <span>Woods family server <small>(chat.lastnpc.com)</small></span></label>
    <label class="su-check"><input type="radio" name="srv" value="other" id="pick-other"> <span>Other</span></label>
    <div id="pick-other-box" hidden><label for="pick-url">Server address</label><input id="pick-url" inputmode="url" autocapitalize="off" spellcheck="false" maxlength="200" placeholder="chat.example.org"></div>
    <button id="pick-go" type="submit">Continue</button>
    <p class="fd-alt"><button id="pick-back" class="linkish" type="button">Back</button></p>
    <div class="msg" id="pick-error" role="alert"></div>
  </form>

  <form id="signup" novalidate data-step="1">
    <span class="bc-icon fd-card-icon"><img src="/favicon.png" alt="" width="64" height="64"></span>
    <p class="fd-title" id="su-title">Create your account</p>
    <div id="su-step1">
      <label for="su-username">Choose a username</label>
      <input id="su-username" autocomplete="username" maxlength="32" autocapitalize="off" spellcheck="false" aria-describedby="su-hint">
      <p class="hint" id="su-hint">3&ndash;32 letters, numbers, dots, dashes or underscores.</p>
    </div>
    <div id="su-choose" hidden>
      <p class="fd-who">Username: <b id="su-who1"></b> <button id="su-change1" class="linkish" type="button">Change</button></p>
      <p class="fd-ask">Where will you chat?</p>
      <div class="fd-opts" role="group" aria-label="Where will you chat?">
        <button type="button" class="fd-opt" id="opt-invite"><span class="fd-opt-ico" aria-hidden="true">__ICO_INVITE__</span><span class="fd-opt-txt"><b>I have an invite</b><small>Paste the link someone sent you</small></span></button>
        <button type="button" class="fd-opt" id="opt-find"><span class="fd-opt-ico" aria-hidden="true">__ICO_FIND__</span><span class="fd-opt-txt"><b>Find my family or group</b><small>Search the BranchChat directory</small></span></button>
        <button type="button" class="fd-opt" id="opt-public"><span class="fd-opt-ico" aria-hidden="true">__ICO_PUBLIC__</span><span class="fd-opt-txt"><b>Use the public server</b><small>Free account on __SERVER__</small></span></button>
      </div>
    </div>
    <div id="su-invite" hidden>
      <p class="fd-who">Username: <b id="su-who2"></b> <button id="su-change2" class="linkish" type="button">Change</button></p>
      <label for="su-invite-link">Invite link</label>
      <input id="su-invite-link" inputmode="url" autocapitalize="off" spellcheck="false" maxlength="2000" placeholder="https://branchchat.app/join?s=…" aria-describedby="su-invite-hint">
      <p class="hint" id="su-invite-hint">Paste the whole link from the email or message.</p>
      <p class="fd-alt"><button id="su-invite-back" class="linkish" type="button">Back</button></p>
    </div>
    <div id="su-step2" hidden>
      <p class="fd-who">Username: <b id="su-who"></b> <button id="su-change" class="linkish" type="button">Change</button></p>
      <label for="su-email">Email</label>
      <input id="su-email" type="email" autocomplete="email" maxlength="200" aria-describedby="su-email-hint">
      <p class="hint fd-sr" id="su-email-hint">For sign-in codes on a new device and password resets. Not shown to anyone.</p>
      <label for="su-password">Password <span class="fd-note">(at least 8 characters)</span></label>
      <input id="su-password" type="password" autocomplete="new-password" maxlength="200">
      <label for="su-again">Repeat password</label>
      <input id="su-again" type="password" autocomplete="new-password" maxlength="200">
      <label class="su-check"><input id="su-agree" type="checkbox"> <span>I am 13 or older and I will keep it respectful.</span></label>
      <div id="su-turnstile" class="su-turnstile" hidden></div>
    </div>
    <button id="su-go" type="submit">Create account</button>
    <p class="fd-alt">Already have an account? <button id="su-to-login" class="linkish" type="button">Log in</button></p>
    <div class="msg" id="su-error" role="alert"></div>
  </form>

  <form id="login" hidden>
    <span class="bc-icon fd-card-icon"><img src="/favicon.png" alt="" width="64" height="64"></span>
    <p class="fd-title">Welcome back</p>
    <p class="secure fd-sr"><span id="secure-led" class="led"></span><span id="secure-text">Not a secure connection</span></p>
    <label for="username">Username (not your display name)</label>
    <input id="username" name="username" autocomplete="username" autocapitalize="off" spellcheck="false" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Log in</button>
    <button id="forgot" class="linkish fd-forgot" type="button">Forgot password?</button>
    <p class="fd-alt">New here? <button id="to-signup" class="linkish" type="button">Create account</button></p>
    <div class="msg" id="error" role="alert"></div>
    <p class="ok" id="login-note" role="status"></p>
  </form>

  <form id="own" hidden novalidate>
    <p class="fd-title">Use your own server</p>
    <label for="own-addr">Server address</label>
    <input id="own-addr" inputmode="url" autocomplete="url" autocapitalize="off" spellcheck="false" maxlength="200" placeholder="chat.example.org" aria-describedby="own-hint">
    <p class="hint" id="own-hint">The address your family, church or group uses for BranchChat.</p>
    <button id="own-go" type="submit">Continue</button>
    <p class="fd-alt"><button id="own-back" class="linkish" type="button">Back</button></p>
    <div class="msg" id="own-error" role="alert"></div>
  </form>
%s
  <div class="fd-server" id="fd-server">
    %s
    <span class="grow">Server: <b>%s</b></span>
    <a href="#own" id="fd-own" role="button">Use my own server</a>
  </div>
  <nav class="fd-explore" id="fd-explore" aria-label="Explore BranchChat">
__FD_DIR__    <a href="#lobby" id="fd-lobby" role="button"><span aria-hidden="true">__ICO_PUBLIC__</span>Try the public Lobby</a>
  </nav>
  __KOFI_HERO__
  __SIDE__
</main>
<footer class="fd-foot" id="bc-footer"><p class="fd-links"><a href="/patch-notes" class="ver" title="Patch notes">%s</a> &middot; <a href="/host">Host your own / Get the server app</a>%s</p>
__LEGAL__</footer>

__FD_BROWSE__
<div class="fd-modal" id="fd-hand" hidden>
  <div class="fd-modal-card" role="dialog" aria-modal="true" aria-labelledby="hand-title">
    <img id="hand-icon" src="/favicon.png" alt="" width="72" height="72">
    <p class="org" id="hand-name"></p>
    <h2 id="hand-title" aria-live="polite" aria-atomic="true">Loading sign-in page</h2>
    <p class="host" id="hand-host"></p>
    <span class="fd-spin" aria-hidden="true"></span>
  </div>
</div>
""" % ("\n".join("  " + f for f in later_forms if f), SERVER_ICON, _e(server), _e(version),
       # mockup Oct 19: the client "Get the app" link is gone (the left column carries the /get flow);
       # "Host your own / Get the server app" above points at the server download page.
       "" if app else (
                       # Donate link, browsers only (kept out of the store apps, whose rules limit outside payment links).
                       ' <span class="fd-donate">&middot; <a href="https://ko-fi.com/branchchat" id="fd-kofi" target="_blank" rel="noopener noreferrer">Support BranchChat &#9749;</a></span>'))


# Oct 19 2026 (mockup, frontdoor-get-mockup): the download column now carries the full /get download flow, the same
# markup and script the /get page uses (downloads.section without its hero, which the front door already shows):
# OS-detected main button, every ready platform tile (yours first), the Linux format menu (#bc-linux: .deb/.rpm/AppImage/
# tar.gz with copyable install steps), the "on the way" line, "use it in your browser" and "See server downloads".
# On wide screens this column is on the LEFT and sign-in on the RIGHT (app-frontdoor.css).
def _side(app):
    if app:
        return ""
    return '<aside class="fd-side" id="fd-side" aria-label="Get BranchChat"><div class="fd-get">%s</div></aside>' % downloads.section(hero=True)


# Fill in the icon and server-name placeholders in the front door HTML.
def _icons(text):
    return (text.replace("__ICO_INVITE__", ICO_INVITE).replace("__ICO_FIND__", ICO_FIND).replace("__ICO_PUBLIC__", ICO_PUBLIC)
            .replace("__SERVER__", _e(_server_label())))


def page(body, csp, user_agent, query=None):
    """login.html -> the front door. Returns (body, csp, app_kind)."""
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return body, csp, ""
    app = app_kind(user_agent, query)
    version = publicmode._version_of(text) or ""
    # Pull out the existing forms: sign-in is rebuilt above; the later steps (authenticator, reset, email code)
    # are kept exactly as login.js expects them.
    text, _login = _cut(text, '<form id="login">')
    later = []
    for marker in ('<form id="factor"', '<form id="reset"', '<form id="unlock"'):
        text, f = _cut(text, marker)
        later.append(f)
    # The existing footer (verse, memorial, "trademark"): 12:10 PM - keep it, small, below the
    # sign-in. Browsers get all three lines; the apps get the one compact legal line. Moved into the front-door footer.
    # Oct 18 2026 footer hotfix: login.html now marks its footer class="bc-mini" (just the trademark line; the verse and
    # memorial moved to the About page), so match the footer tag with or without extra attributes.
    m = re.search(r'<footer id="bc-footer"[^>]*>(.*?)</footer>\s*', text, flags=re.S)
    legal = ""
    if m:
        text = text[:m.start()] + text[m.end():]
        inner = m.group(1)
        if app:
            legal = "\n".join(re.findall(r'<p class="legal">.*?</p>', inner, flags=re.S))
        else:
            legal = inner.strip()
    attrs = 'data-bc-dl="1" data-signup="1" data-bc-fd="1"' + (' data-bc-app="%s"' % app if app else "")
    text = text.replace('<html lang="en"', '<html lang="en" ' + attrs, 1)
    text = text.replace("A private, invite-only chat for family and friends.",
                        "Free, end-to-end encrypted chat with friends and family.")
    text = text.replace('<meta name="viewport" content="width=device-width, initial-scale=1">',
                        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">', 1)
    head = ('<script src="/static/fd-early.js?v=%s"></script>\n<script src="/static/scheme.js?v=2"></script>\n'
            '<link rel="stylesheet" href="/static/downloads.css?v=%s">\n'
            '<link rel="stylesheet" href="/static/app-frontdoor.css?v=%s">' % (ASSET_V, downloads.ASSET_V, ASSET_V))
    if not app:   # the /get download flow: Linux menu, OS detection (downloads.js) and the server-downloads dialog
        head += "\n" + publicmode.host_head()
    if publicmode.TURNSTILE:
        head += '\n<meta name="bc-turnstile" content="%s">' % _e(publicmode.TURNSTILE_SITEKEY)
        head += '\n<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async defer></script>'
        csp = csp.replace("script-src 'self'", "script-src 'self' https://challenges.cloudflare.com", 1)
        csp = csp.replace("frame-src ", "frame-src https://challenges.cloudflare.com ", 1)
        csp = csp.replace("connect-src 'self'", "connect-src 'self' https://challenges.cloudflare.com", 1)
    # "Use my own server" reads that server's /.well-known/branchchat and icon straight from it, like the
    # invite landing page (/join) already does: allow https: (and http: only when the home allows http servers).
    extra = " https:" + (" http:" if directory.ALLOW_HTTP else "")
    csp = csp.replace("connect-src 'self'", "connect-src 'self'" + extra, 1)
    csp = csp.replace("img-src 'self'", "img-src 'self'" + extra, 1)
    # kofi-modal: Support links open the Ko-fi tip panel in a dialog (browsers; the script skips the apps).
    head += ('\n<link rel="stylesheet" href="/static/kofi-modal.css?v=%s">\n<script defer src="/static/kofi-modal.js?v=%s"></script>' % (ASSET_V, ASSET_V))
    text = text.replace("</head>", head + "\n</head>", 1)
    if not publicmode.OPEN_SIGNUP:
        text = text.replace('data-signup="1"', 'data-signup="0"', 1)
    # directory-signin: the gate button says "Sign in here" in browsers; the apps keep "Sign in on web".
    # directory-signin: browsers get the Browse groups pop-up behind "Sign in here" (so the separate Browse groups
    # button goes); the apps keep the old sign-in and their Browse groups button.
    main_html = _main(version, app, later).replace("__SIGNIN_LABEL__", "Sign in on web" if app else "Sign in here")
    if app:
        main_html = main_html.replace("__FD_DIR__", '    <a href="/directory" id="fd-dir"><span aria-hidden="true">__ICO_FIND__</span>Browse groups</a>\n').replace("__FD_BROWSE__", "")
    else:
        main_html = main_html.replace("__FD_DIR__", "").replace("__FD_BROWSE__", FD_BROWSE)
    main = _icons(main_html.replace("__SIDE__", _side(app))).replace("__LEGAL__", '<div class="fd-legal">%s</div>' % legal if legal else "").replace("__KOFI_HERO__", KOFI_HERO)
    text = text.replace("<body>", "<body>\n" + main, 1)
    marker = '<script type="module" src="/static/login.js'
    text = text.replace(marker, '<script type="module" src="/static/frontdoor.js?v=%s"></script>\n%s' % (ASSET_V, marker), 1)
    text = text.replace(marker, '<script type="module" src="/static/fd-gate.js?v=%s"></script>\n%s' % (ASSET_V, marker), 1)   # 26.10.12
    if not app:   # directory-signin: Sign in opens the Browse groups pop-up (browsers only)
        text = text.replace(marker, '<script defer src="/static/fd-browse.js?v=%s"></script>\n%s' % (ASSET_V, marker), 1)
    if not app:   # browsers only: iPhone / iPad / Mac get install steps in place of "Get the app" (fd-install.js)
        text = text.replace(marker, '<script defer src="/static/fd-install.js?v=%s"></script>\n%s' % (ASSET_V, marker), 1)
        text = text.replace("</body>", '<script type="module" src="/static/downloads.js?v=%s"></script>\n</body>' % downloads.ASSET_V, 1)
    return text.encode("utf-8"), csp, app
