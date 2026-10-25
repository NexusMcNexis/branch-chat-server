"""Content filters for the public Branch Chat server (branchchat.app): links in the Lobby, mature language.

Pure functions, standard library only, no state. publicmode.py decides when they run (never on a private server).
The browser runs the same rules (static/contentfilter.js) for instant feedback; the server is authoritative.
Both read static/filter-data.json, built by tools/build-filter-data.py (sources and licences in
docs/FILTER-SOURCES.md), so the two sides cannot drift apart.

Normalization (shared by both filters), one output character per step, each remembering which characters of
the original text it came from, so a match can be cut out of the original:
  1. zero-width and other invisible format characters (Unicode category Cf, U+034F, U+180E, ...) are dropped
  2. NFKC (fullwidth, mathematical, circled and other compatibility forms become plain letters)
  3. lowercase, accents removed (NFD, drop combining marks)
  4. Unicode confusables (UTS #39) that look like a-z, 0-9 or . : / @ become those (Cyrillic a/o/e, Greek, ...)
  5. links only: "dot", "(dot)", "[.]", "{.}", " . " become "."; "(at)" becomes "@"; "h t t p s : / /" style
     spaced-out letters are joined; "c.o.m"-style single-letter tails are joined
  6. language only: leetspeak (4/@ a, 3 e, 1/! i, 0 o, 5/$ s, 7/+ t, 8 b, 9 g), punctuation inside a word
     (f.u.c.k, f-u-c-k), single letters with spaces (f u c k), and repeated letters (fuuuck) are matched too.
A match counts as an evasion when it needed step 1, 2, 4, 5 or 6 (except repeated letters and self-censoring
like f**k): the plain text did not look like a link or a listed word but the normalized text did.
"""
import json
import os
import re
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "static", "filter-data.json"), encoding="utf-8") as _fh:
    DATA = json.load(_fh)

TLDS = frozenset(DATA["tlds"])
FILE_TLDS = frozenset(DATA["file_tlds"])
RISKY_TLDS = frozenset(DATA["risky_tlds"])
WORD_TLDS = frozenset(DATA["word_tlds"])
STOPWORDS = frozenset(DATA["stopwords"])
SHORTENERS = frozenset(DATA["shorteners"])
CONFUSABLES = DATA["confusables"]
LEET = DATA["leet"]
ALLOW = tuple(DATA["allow"])
STRONG = tuple(DATA["strong"])

LINK_TEXT = "[link removed]"
MASK = "\u2217"

# Technique bits recorded per normalized character.
ZW, COMPAT, CONFUSABLE, ACCENT, LEETF, DOTWORD, SPACING, PUNCT, SPACED, WILD = (1 << i for i in range(10))
EVASIVE_LINK = ZW | COMPAT | CONFUSABLE | LEETF | DOTWORD | SPACING | SPACED
EVASIVE_WORD = ZW | COMPAT | CONFUSABLE | LEETF | PUNCT | SPACED
TECH_NAMES = ((ZW, "zero-width"), (COMPAT, "fullwidth/compat"), (CONFUSABLE, "homoglyph"), (ACCENT, "accents"),
              (LEETF, "leetspeak"), (DOTWORD, "dot-words"), (SPACING, "spacing"), (PUNCT, "punctuation-split"),
              (SPACED, "spaced-letters"), (WILD, "self-censored"))
PLACEHOLDER = "\ufffc"  # astral characters (emoji) in the normalized text, so JS and Python index the same way


def techniques(bits):
    return [name for bit, name in TECH_NAMES if bits & bit]


def _invisible(ch):
    o = ord(ch)
    if o in (0x034F, 0x180E, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x17B4, 0x17B5):
        return True
    if 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF:
        return True
    return unicodedata.category(ch) == "Cf"


class _M(object):
    """Normalized text: chars[i] came from original[spans[i][0]:spans[i][1]], with technique bits flags[i]."""
    __slots__ = ("c", "s", "f")

    def __init__(self):
        self.c, self.s, self.f = [], [], []

    def add(self, ch, span, flag):
        self.c.append(ch)
        self.s.append(span)
        self.f.append(flag)

    def text(self):
        return "".join(self.c)


def normalize(text):
    m = _M()
    for i, ch in enumerate(text):
        if _invisible(ch):
            if m.f:
                m.f[-1] |= ZW
            continue
        flag = 0
        nf = unicodedata.normalize("NFKC", ch)
        if nf != ch and ord(ch) > 127:
            flag |= COMPAT
        out = []
        for c2 in unicodedata.normalize("NFD", nf):
            if unicodedata.category(c2) == "Mn":
                flag |= ACCENT
                continue
            out.append(c2)
        low = "".join(out).lower()
        for c3 in low:
            rep = CONFUSABLES.get(c3)
            f2 = flag
            if rep is not None:
                f2 |= CONFUSABLE
            else:
                rep = c3
            for c4 in rep:
                if ord(c4) > 0xFFFF:
                    c4 = PLACEHOLDER
                m.add(c4, (i, i + 1), f2)
    return m


def _sub(m, rx, fn):
    """Regex replace on the normalized text, keeping the map. fn(match) -> (text, flag) or None to keep."""
    text = m.text()
    out = _M()
    pos = 0
    for mt in rx.finditer(text):
        if mt.start() == mt.end():
            continue
        r = fn(mt)
        if r is None or r[0] is None:
            continue
        rep, flag = r
        for k in range(pos, mt.start()):
            out.add(m.c[k], m.s[k], m.f[k])
        a, b = mt.start(), mt.end()
        span = (m.s[a][0], m.s[b - 1][1])
        fl = flag
        for k in range(a, b):
            fl |= m.f[k]
        for ch in rep:
            out.add(ch, span, fl)
        pos = b
    for k in range(pos, len(m.c)):
        out.add(m.c[k], m.s[k], m.f[k])
    return out


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
_L = r"[^\W_]"  # a letter or digit in any script (Python has no \p{L}; JS uses [\p{L}\p{N}])
_DOTWORD = re.compile(r"\s*(?:[(\[{<]\s*(?:dot|d0t|dawt|period|punto|punkt|\.)\s*[)\]}>]|\b(?:dot|d0t|dawt)\b)\s*")
_ATWORD = re.compile(r"\s*[(\[{<]\s*(?:at|@)\s*[)\]}>]\s*")
_SCHEME_SPACED = re.compile(r"\b(h\s*t\s*t\s*p\s*s?|f\s*t\s*p)\s*:\s*/\s*/\s*")
_SPACE_DOT = re.compile(r"(?<=\S)[ \t]+\.[ \t]*(?=\S)|(?<=[^\W_])\.[ \t]+(?=(" + "|".join(sorted(RISKY_TLDS, key=len, reverse=True)) + r")(?![^\W_]))")
_SPACED_RUN = re.compile(r"(?<![^\W_])(?:[^\W_][ \t_*\-,.]{1,3}){3,}[^\W_]+(?![^\W_])")
_SINGLE_DOTS = re.compile(r"(?<![^\W_.])(?:[^\W_]\.){3,}[^\W_](?![^\W_])")
_MD = re.compile(r"\[[^\]\n]{0,300}\]\(\s*[^)\s]*[.:/][^)]*\)")
_HTML = re.compile(r"<a\b[^>]*>.*?</a\s*>|<[a-z][^>]*\b(?:href|src)\s*=[^>]*>", re.S)
_SCHEME = re.compile(r"\b[a-z][a-z0-9+.\-]{1,15}://\S*|\b(?:mailto|javascript|data|tel|sms|magnet|intent|market|file):[^\s]+")
_WWW = re.compile(r"\bwww\d{0,3}\.\S*")
_EMAIL = re.compile(r"(?<![^\s(\[<\"'])[a-z0-9._%+\-]+@(?:" + _L + r"(?:[\w\-]{0,61}" + _L + r")?\.)+" + _L + r"{2,63}\b")
_IPV4 = re.compile(r"(?<![\d.v])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])(?::\d{1,5})?(?:/\S*)?")
_IPV6 = re.compile(r"(?<![0-9a-f:])[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![0-9a-f:])")
_SCHEME_DISGUISE = re.compile(r"\bh[x*.\-_t]{2}p(s?)(?=\s*:\s*/\s*/)")
KNOWN_SCHEMES = frozenset("http https ftp ftps sftp ws wss git ssh irc ircs rtsp mms steam discord tg whatsapp file".split())
_DOMAIN = re.compile(r"(?<![\w@.\-])((?:" + _L + r"(?:[\w\-]{0,61}" + _L + r")?\.)+)(" + _L + r"[\w@$\-]{1,62})(:\d{1,5})?([/?#][^\s]*)?")
_SHORT_SPACED = re.compile(r"\b(" + "|".join(re.escape(s.split(".")[0]) + r"[\s._\-]*" + re.escape(s.split(".", 1)[1]) for s in sorted(SHORTENERS)) + r")\s*/\s*\S+")


def _leet(s):
    return "".join(LEET.get(c, c) for c in s)


def _domain_ok(m, mt, base_flags, text):
    """Is this domain-shaped match a link? Returns flag bits to add (>= 0) or -1."""
    host_labels = mt.group(1)[:-1].split(".") + [mt.group(2)]
    host = ".".join(host_labels)
    tld = mt.group(2)
    port, path = mt.group(3), mt.group(4)
    extra = 0
    if "_" in host:
        return -1
    if tld in TLDS:
        pass
    elif re.search(r"[a-z]", tld) and (_leet(tld) in RISKY_TLDS or _leet(tld.replace("@", "o")) in RISKY_TLDS):
        extra |= LEETF
        tld = _leet(tld) if _leet(tld) in RISKY_TLDS else _leet(tld.replace("@", "o"))
    elif tld.startswith("xn--"):
        pass
    else:
        return -1
    short = host in SHORTENERS or ".".join(host_labels[-2:]) in SHORTENERS
    strong = bool(port or (path and len(path) > 1) or len(host_labels) >= 3 or short)
    if all(len(x) == 1 for x in host_labels[:-1]) and len(tld) <= 2 and not strong:
        return -1  # e.g. "u.s", "a.m"
    t = _leet(tld) if tld not in TLDS else tld
    if not strong and t in FILE_TLDS:
        return -1
    if not strong and t in WORD_TLDS:
        return -1
    if not strong and t not in RISKY_TLDS and len(host_labels) == 2:
        # "Done.Now", "home.It": a full stop and a capitalised word, not a link.
        s0 = m.s[mt.start(2)][0]
        if s0 + 1 < len(text) and text[s0].isupper() and text[s0 + 1].islower():
            return -1
    flags = base_flags | extra
    if flags & (DOTWORD | SPACING | SPACED):
        if not (t in RISKY_TLDS or short or (path and len(path) > 1)):
            return -1
        if host_labels[-2] in STOPWORDS:
            return -1
    return extra


def find_links(text):
    """List of {"start","end","kind","evasion","tech","host"} in original-text positions, merged and sorted."""
    if not isinstance(text, str) or not text:
        return []
    m = normalize(text)
    # Step 5: undo the common disguises (each marks the characters it touched).
    m = _sub(m, _SCHEME_DISGUISE, lambda mt: (None if mt.group(0) in ("http", "https") else ("http" + mt.group(1), LEETF)))
    m = _sub(m, _SCHEME_SPACED, lambda mt: (re.sub(r"\s+", "", mt.group(0)), SPACING if re.search(r"\s", mt.group(0)) else 0))
    m = _sub(m, _DOTWORD, lambda mt: (".", DOTWORD))
    m = _sub(m, _ATWORD, lambda mt: ("@", DOTWORD))
    m = _sub(m, _SPACE_DOT, lambda mt: (".", SPACING))

    def spaced(mt):
        s = mt.group(0)
        joined = re.sub(r"[ \t_*\-,]+", "", s)
        if joined == s or not re.search(r"[ \t_*\-,]", s):
            return None
        return joined, SPACED
    m = _sub(m, _SPACED_RUN, spaced)

    def single_dots(mt):
        labels = mt.group(0).split(".")
        for k in (4, 3, 2):
            if len(labels) > k + 1 and "".join(labels[-k:]) in RISKY_TLDS:
                return "".join(labels[:-k]) + "." + "".join(labels[-k:]), SPACED
        return None
    m = _sub(m, _SINGLE_DOTS, single_dots)
    t = m.text()
    hits = []

    def add(a, b, kind, extra=0, host=""):
        fl = extra
        for k in range(a, b):
            fl |= m.f[k]
        hits.append({"start": m.s[a][0], "end": m.s[b - 1][1], "kind": kind, "bits": fl, "host": host})

    taken = [False] * len(t)

    def free(a, b):
        return not any(taken[a:b])

    def take(a, b):
        for k in range(a, b):
            taken[k] = True

    for rx, kind in ((_MD, "markdown"), (_HTML, "html"), (_SCHEME, "url"), (_WWW, "www"), (_EMAIL, "email"), (_SHORT_SPACED, "shortener")):
        for mt in rx.finditer(t):
            a, b = mt.start(), mt.end()
            if a == b or not free(a, b):
                continue
            host = _host_of(mt.group(0))
            extra = 0
            if kind == "url":
                sm = re.match(r"([a-z][a-z0-9+.\-]*):", mt.group(0))
                if sm and sm.group(1) not in KNOWN_SCHEMES and sm.group(1) not in ("mailto", "javascript", "data", "tel", "sms", "magnet", "intent", "market"):
                    extra = LEETF
            add(a, b, kind, extra, host)
            take(a, b)
    for mt in _IPV4.finditer(t):
        if all(int(x) <= 255 for x in mt.group(1).split(".")) and free(mt.start(), mt.end()):
            add(mt.start(), mt.end(), "ip", 0, mt.group(1))
            take(mt.start(), mt.end())
    for mt in _IPV6.finditer(t):
        s = mt.group(0)
        if ("::" in s or s.count(":") >= 5) and s.count("::") <= 1 and len(s) >= 5 and re.search(r"[0-9a-f]{2}", s) and not re.match(r"^\d{1,2}(:\d{2}){1,2}$", s) and free(mt.start(), mt.end()):
            add(mt.start(), mt.end(), "ip", 0, "ipv6")
            take(mt.start(), mt.end())
    for mt in _DOMAIN.finditer(t):
        a, b = mt.start(), mt.end()
        if not free(a, b):
            continue
        base = 0
        for k in range(a, b):
            base |= m.f[k]
        extra = _domain_ok(m, mt, base, text)
        if extra < 0:
            continue
        # A trailing full stop or bracket belongs to the sentence, not the link.
        while b > a and t[b - 1] in ".,;:!?)]}'\"":
            b -= 1
        add(a, b, "domain", extra, (mt.group(1) + mt.group(2)).lower())
        take(a, b)
    hits.sort(key=lambda h: h["start"])
    merged = []
    for h in hits:
        if merged and h["start"] < merged[-1]["end"]:
            last = merged[-1]
            last["end"] = max(last["end"], h["end"])
            last["bits"] |= h["bits"]
            continue
        merged.append(h)
    for h in merged:
        h["evasion"] = bool(h["bits"] & EVASIVE_LINK)
        h["tech"] = techniques(h["bits"])
        h["host"] = _host_only(h["host"])
        del h["bits"]
    return merged


def _host_of(link):
    """Host part of a matched link: after the scheme and any user:password@, before the port or path."""
    hm = re.search(r"(?:://|www\d{0,3}\.|@)([^/\s?#\])>\"']+)", link)
    if not hm:
        return ""
    return hm.group(1).split("@")[-1].split(":")[0]


def _host_only(host):
    """What moderation keeps of a link: the host name only (no path, query or user part), at most 80 chars."""
    host = (host or "").split("@")[-1].strip(".").lower()
    return host[:80]


def strip_links(text):
    """(text with each link replaced by LINK_TEXT, hits)."""
    hits = find_links(text)
    if not hits:
        return text, []
    out, pos = [], 0
    for h in hits:
        out.append(text[pos:h["start"]])
        out.append(LINK_TEXT)
        pos = h["end"]
    out.append(text[pos:])
    return "".join(out), hits


# ---------------------------------------------------------------------------
# Mature language
# ---------------------------------------------------------------------------
_TOKCH = re.compile(r"[^\W_]|[@$!|+]")
_INNER = set(".-_*'\u2019~#")


def _runs(s):
    out = []
    for ch in s:
        if out and out[-1][0] == ch:
            out[-1][1] += 1
        else:
            out.append([ch, 1])
    return out


def _squeeze(s):
    return "".join(ch for ch, _ in _runs(s))


def _skel(word):
    """Entry or token letters: lowercase, leet mapped, only a-z0-9 (others dropped)."""
    return "".join(c for c in _leet(word) if c.isalnum())


_RAW = set()     # list words exactly as written ("sh1t" is listed, so writing it is not an evasion)
_ENTRIES = {}   # squeezed skeleton -> [(skeleton, display word)]
_PHRASES = []   # [(list of word skeletons, word)]
for _w in DATA["words"]:
    _parts = [p for p in re.split(r"[\s]+", _w) if p]
    if len(_parts) > 1:
        _PHRASES.append(([_skel(unicodedata.normalize("NFKC", p).lower()) for p in _parts], _w))
        continue
    _s = _skel(unicodedata.normalize("NFKC", _w).lower())
    _RAW.add(_w)
    if _s:
        _ENTRIES.setdefault(_squeeze(_s), []).append((_s, _w))
_STRONG_SQ = tuple(_squeeze(_skel(w)) for w in STRONG)
_ALLOW_SET = frozenset(ALLOW)
_PAIR_OK = ("fuck", "shit", "cunt", "bitch", "dick", "cock", "pussy", "whore", "slut", "nigger", "nigga", "faggot", "twat", "wank")
_COMMON = frozenset("it is as a i in on to of he me we us so no do go up at or an by my am be oh ok hi".split())


def _runs_cover(token, entry):
    """token matches entry when the letters are the same and each run is at least as long (fuuuck, asss)."""
    rt, re_ = _runs(token), _runs(entry)
    if len(rt) != len(re_):
        return False
    return all(a[0] == b[0] and a[1] >= b[1] for a, b in zip(rt, re_))


def _lookup(sk):
    for entry, word in _ENTRIES.get(_squeeze(sk), ()):
        if _runs_cover(sk, entry):
            return entry
    if sk.endswith("s") and len(sk) > 3:
        for entry, word in _ENTRIES.get(_squeeze(sk[:-1]), ()):
            if _runs_cover(sk[:-1], entry):
                return entry
    return None


def _allowed_at(sk, a, b):
    """Is sk[a:b] inside an allowlisted word that occurs in sk?"""
    for w in ALLOW:
        start = sk.find(w)
        while start >= 0:
            if start <= a and b <= start + len(w):
                return True
            start = sk.find(w, start + 1)
    return False


def _tokens(m):
    """Words in the normalized text: [(start, end, skeleton, bits)], letters/digits/leet joined by inner punctuation."""
    t = m.text()
    toks = []
    i, n = 0, len(t)
    while i < n:
        if not _TOKCH.match(t[i]):
            i += 1
            continue
        j = i
        chars = []
        bits = 0
        wild = False
        while j < n:
            ch = t[j]
            if _TOKCH.match(ch):
                if ch in LEET or (ch.isdigit() and ch in LEET):
                    chars.append(LEET[ch])
                    bits |= LEETF
                elif ch.isalnum():
                    chars.append(ch)
                else:
                    break
                bits |= m.f[j]
                j += 1
                continue
            if ch in _INNER and chars:
                k = j
                while k < n and k - j < 3 and t[k] in _INNER:
                    k += 1
                if k < n and _TOKCH.match(t[k]):
                    for q in range(j, k):
                        if t[q] in "*#":
                            wild = True
                            chars.append("*")
                        elif t[q] not in "'\u2019":
                            bits |= PUNCT
                    j = k
                    continue
            break
        word = "".join(chars)
        if re.search(r"[a-z]", word.replace("*", "")) and any(not c.isdigit() for c in t[i:j] if c.isalnum()):
            if wild:
                bits |= WILD
            # Leet only counts when the token has real letters next to it ("sh1t", not "2024").
            toks.append([i, j, word, bits])
        i = j if j > i else i + 1
    for tk in toks:
        raw = t[tk[0]:tk[1]]
        if not any(ch in LEET for ch in raw) or raw in _RAW or (raw.endswith("s") and raw[:-1] in _RAW):
            tk[3] &= ~LEETF
    return toks


_EDGE = "@$!|+"


def _edge_syms(t, a, b):
    """How many leet symbols (not letters or digits) start and end the token t[a:b]."""
    lead = 0
    while a + lead < b and t[a + lead] in _EDGE:
        lead += 1
    trail = 0
    while b - trail - 1 > a + lead and t[b - trail - 1] in _EDGE:
        trail += 1
    return lead, trail


def find_words(text):
    """Mature-language matches: [{"start","end","word","mask","evasion","tech"}] in original positions."""
    if not isinstance(text, str) or not text:
        return []
    m = normalize(text)
    toks = _tokens(m)
    t = m.text()
    hits = []
    used = [False] * len(toks)

    def hit(i0, i1, entry, bits):
        a, b = toks[i0][0], toks[i1][1]
        if b <= a:
            return
        for k in range(i0, i1 + 1):
            used[k] = True
        fl = bits
        for k in range(a, b):
            fl |= m.f[k]
        hits.append({"start": m.s[a][0], "end": m.s[b - 1][1], "word": entry, "bits": fl,
                     "mask": entry[0] + MASK * max(2, len(entry) - 1)})

    # Phrases first ("son of a bitch", "blow job").
    for parts, word in _PHRASES:
        L = len(parts)
        for i in range(len(toks) - L + 1):
            if any(used[i:i + L]):
                continue
            if all(_runs_cover(toks[i + k][2], parts[k]) or toks[i + k][2] == parts[k] for k in range(L)):
                hit(i, i + L - 1, "".join(parts), 0)
    # Single letters with spaces between them (f u c k), any window that is a listed word.
    i = 0
    while i < len(toks):
        j = i
        while j < len(toks) and len(toks[j][2]) == 1 and not used[j] and (j == i or re.fullmatch(r"[\s.\-_*,/|]{1,3}", t[toks[j - 1][1]:toks[j][0]])):
            j += 1
        if j - i >= 3:
            letters = "".join(toks[k][2] for k in range(i, j))
            done = False
            for size in range(j - i, 2, -1):
                for a in range(i, j - size + 1):
                    sk = letters[a - i:a - i + size]
                    entry = _lookup(sk)
                    if entry and not any(used[a:a + size]):
                        hit(a, a + size - 1, entry, SPACED)
                        done = True
                if done:
                    break
            i = j
        else:
            i = max(j, i + 1)
    # Whole words, wildcards, strong substrings.
    for idx, (a, b, sk, bits) in enumerate(toks):
        if used[idx] or sk in _ALLOW_SET:
            continue
        if "*" in sk:
            core = sk.replace("*", "")
            if len(core) < 1:
                continue
            rx = re.compile("^" + re.escape(sk).replace(r"\*", "[a-z]") + "s?$")
            found = None
            for lst in _ENTRIES.values():
                for entry, word in lst:
                    if len(entry) >= 3 and rx.match(entry):
                        found = entry
                        break
                if found:
                    break
            if found:
                hit(idx, idx, found, WILD)
            continue
        entry = _lookup(sk)
        if entry:
            hit(idx, idx, entry, bits)
            continue
        # 26.10.13.13: "bitch!", "shit?!", "!fuck": a leet symbol at the very start or end of a word is usually
        # punctuation, not a letter ("!" read as "i" turned "bitch!" into "bitchi" and nothing matched).
        # Try again without those edge symbols; the mask then covers only the word and keeps the "!".
        lead, trail = _edge_syms(t, a, b)
        if lead or trail:
            found = None
            for la, tr in ((0, trail), (lead, 0), (lead, trail)):
                if (la or tr) and len(sk) - la - tr >= 3:
                    core = sk[la:len(sk) - tr]
                    if core not in _ALLOW_SET:
                        found = _lookup(core)
                        if found:
                            break
            if found:
                toks[idx] = [a + la, b - tr, sk[la:len(sk) - tr], bits]
                hit(idx, idx, found, bits)
                continue
        sq = _squeeze(sk)
        for strong in _STRONG_SQ:
            pos = sq.find(strong)
            if pos >= 0 and not _allowed_at(sk, sk.find(strong[0]), len(sk)) and not any(w in sk for w in ALLOW if strong in _squeeze(w)):
                hit(idx, idx, strong, bits)
                break
    # Two short pieces of one word ("fu ck", "bi tch").
    for idx in range(len(toks) - 1):
        if used[idx] or used[idx + 1]:
            continue
        a, b = toks[idx][2], toks[idx + 1][2]
        gap = t[toks[idx][1]:toks[idx + 1][0]]
        if 2 <= len(a) <= 4 and 2 <= len(b) <= 4 and a not in _COMMON and b not in _COMMON and re.fullmatch(r"[\s.\-_,]{1,2}", gap):
            entry = _lookup(a + b)
            if entry and entry in _PAIR_OK:
                hit(idx, idx + 1, entry, SPACED)
    hits.sort(key=lambda h: h["start"])
    out = []
    for h in hits:
        if out and h["start"] < out[-1]["end"]:
            continue
        h["evasion"] = bool(h["bits"] & EVASIVE_WORD) and not (h["bits"] & WILD and not h["bits"] & (EVASIVE_WORD & ~PUNCT))
        h["tech"] = techniques(h["bits"])
        del h["bits"]
        out.append(h)
    return out


def mask_words(text):
    """(text with each match masked, e.g. f***, hits)."""
    hits = find_words(text)
    if not hits:
        return text, []
    out, pos = [], 0
    for h in hits:
        out.append(text[pos:h["start"]])
        out.append(h["mask"])
        pos = h["end"]
    out.append(text[pos:])
    return "".join(out), hits


def name_problem(name, links=True):
    """Why a username / display name / group name is refused: None, or {"kind","evasion","tech"}."""
    if not isinstance(name, str) or not name.strip():
        return None
    if links:
        lh = find_links(name)
        if lh:
            return {"kind": "link", "evasion": any(h["evasion"] for h in lh), "tech": sorted({x for h in lh for x in h["tech"]})}
    wh = find_words(name)
    if wh:
        return {"kind": "language", "evasion": any(h["evasion"] for h in wh), "tech": sorted({x for h in wh for x in h["tech"]})}
    # Names run words together ("bigfuckerjoe"): look for listed words of 4+ letters anywhere, minus allowed words.
    m = normalize(name)
    sk = _skel("".join(c for c in m.text() if c.isalnum() or c in LEET))
    sq = _squeeze(sk)
    for key, lst in _ENTRIES.items():
        for entry, word in lst:
            if len(entry) < 4:
                continue
            p = sk.find(entry)
            if p < 0:
                continue
            if _allowed_at(sk, p, p + len(entry)):
                continue
            return {"kind": "language", "evasion": False, "tech": []}
    return None


def filter_text(text, links=True, language=True):
    """Lobby text: links removed (when links), then mature words masked (when language). Returns (text, report)."""
    report = {"links": [], "words": []}
    if links:
        text, report["links"] = strip_links(text)
    if language:
        text, report["words"] = mask_words(text)
    return text, report
