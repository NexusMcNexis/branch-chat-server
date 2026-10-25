"""BranchChat licensing pages (26.10 licensing branch).

GET /license                -> LICENSING.md (plain-English summary), rendered as a small HTML page
GET /license/terms          -> LICENSE.md (BranchChat License 2.0)
GET /license/noncommercial, /license/small-business -> LICENSE.md too (old links from 26.10 pre-releases)
GET /license/third-party    -> THIRD-PARTY-NOTICES.md
Files live in web/legal/ (copies of the repo-root files, because deploys ship only web/; tests check they match).
Public on every server (no sign-in), all links relative, so it works on any self-hosted server.
"""
import html, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
LEGAL = os.path.join(HERE, "legal")
PAGES = {
    "/license": ("LICENSING.md", "BranchChat licensing"),
    "/license/terms": ("LICENSE.md", "BranchChat License 2.0"),
    "/license/noncommercial": ("LICENSE.md", "BranchChat License 2.0"),
    "/license/small-business": ("LICENSE.md", "BranchChat License 2.0"),
    "/license/third-party": ("THIRD-PARTY-NOTICES.md", "Third-party notices"),
}
LINKS = {"LICENSING.md": "/license", "LICENSE.md": "/license/terms", "THIRD-PARTY-NOTICES.md": "/license/third-party"}


def _inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)

    def link(m):
        href = m.group(2)
        href = LINKS.get(href, href)
        if not re.match(r"^(https?:|/|#)", href):
            href = "#"
        return '<a href="%s">%s</a>' % (html.escape(href), m.group(1))
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)
    t = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1">\1</a>', t)
    t = re.sub(r"(?<![\"=>])\b([\w.+-]+@[\w-]+\.[\w.]+\w)", r'<a href="mailto:\1">\1</a>', t)
    return t


def render_md(src):
    out, para, lst, table, code = [], [], None, [], None

    def flush():
        nonlocal para, lst, table
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para))); para = []
        if lst:
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _inline(i) for i in lst)); lst = None
        if table:
            rows = [r for r in table if not re.match(r"^\|[\s|:-]+\|$", r)]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            h = "".join("<th>%s</th>" % _inline(c) for c in cells[0])
            b = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in cells[1:])
            out.append('<div class="tbl"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (h, b)); table = []
    for line in src.splitlines():
        if code is not None:
            if line.startswith("```"):
                out.append("<pre>%s</pre>" % html.escape("\n".join(code))); code = None
            else:
                code.append(line)
            continue
        if line.startswith("```"):
            flush(); code = []; continue
        s = line.strip()
        if not s:
            flush(); continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            flush(); n = len(m.group(1))
            slug = re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-")
            out.append('<h%d id="%s">%s</h%d>' % (n, slug, _inline(m.group(2)), n)); continue
        if s.startswith("|"):
            if para or lst:
                flush()
            table.append(s); continue
        if re.match(r"^[-*]\s+", s):
            if para:
                flush()
            lst = (lst or []) + [re.sub(r"^[-*]\s+", "", s)]; continue
        if s.startswith(">"):
            flush(); out.append("<blockquote>%s</blockquote>" % _inline(s.lstrip("> "))); continue
        if lst and line.startswith("  "):
            lst[-1] += " " + s; continue
        para.append(s)
    flush()
    return "\n".join(out)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>%(title)s</title>
<meta name="color-scheme" content="dark light"><style>
body{margin:0;font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;background:#16201a;color:#e6ede8}
main{max-width:760px;margin:0 auto;padding:20px 16px 40px;box-sizing:border-box}
a{color:#8fd3a5}nav{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;margin-bottom:14px}
nav a[aria-current]{font-weight:700;text-decoration:none}h1{font-size:1.45em;line-height:1.25}h2{font-size:1.15em;margin-top:1.6em}
pre{white-space:pre-wrap;font-size:12px;background:rgba(255,255,255,.06);padding:10px;border-radius:8px}
blockquote{margin:1em 0;padding:6px 12px;border-left:3px solid #8fd3a5;background:rgba(255,255,255,.05)}
code{font-size:.9em;overflow-wrap:anywhere}.tbl{overflow-x:auto;max-width:100%%}td,p,li{overflow-wrap:anywhere}table{border-collapse:collapse;font-size:12.5px}
td,th{border:1px solid rgba(255,255,255,.15);padding:5px 7px;vertical-align:top;text-align:left}
@media (prefers-color-scheme: light){body{background:#f6f8f6;color:#1d2a22}a{color:#1f7a44}}
</style></head><body><main>
<nav aria-label="License pages">%(nav)s<a href="/">Return to BranchChat</a></nav>
%(body)s
</main></body></html>"""


def route(handler, method, path):
    """Serve the licensing pages; returns True when the request was handled."""
    if method not in ("GET", "HEAD"):
        return False
    p = path.rstrip("/") or "/"
    if p not in PAGES:
        return False
    fname, title = PAGES[p]
    try:
        with open(os.path.join(LEGAL, fname), encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return False
    nav = "".join('<a href="%s"%s>%s</a>' % (u, ' aria-current="page"' if u == ("/license/terms" if p in ("/license/noncommercial", "/license/small-business") else p) else "", lbl) for u, lbl in (
        ("/license", "Summary"), ("/license/terms", "BranchChat License 2.0"),
        ("/license/third-party", "Third-party notices")))
    body = PAGE % {"title": html.escape(title), "nav": nav, "body": render_md(src)}
    handler.send_bytes(200, body.encode("utf-8"), "text/html; charset=utf-8", [("Cache-Control", "no-cache")])
    return True
