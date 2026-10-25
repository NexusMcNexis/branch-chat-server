/* Branch Chat content filters (public server only): links in the Lobby, mature language.
   A line-by-line copy of web/contentfilter.py (the server's copy is authoritative). Both read
   /static/filter-data.json; server/tests/filter-vectors.json is run against both so they stay identical.
   Only loaded when the public server turns a filter on; private servers never load this file. */
let D = null;
let TLDS, FILE_TLDS, RISKY_TLDS, WORD_TLDS, STOPWORDS, SHORTENERS, CONF, LEET, ALLOW, ALLOW_SET, STRONG_SQ, RAW, ENTRIES, PHRASES;
let RX = {};
export const LINK_TEXT = "[link removed]";
const MASK = "\u2217";
const [ZW, COMPAT, CONFUSABLE, ACCENT, LEETF, DOTWORD, SPACING, PUNCT, SPACED, WILD] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => 1 << i);
const EVASIVE_LINK = ZW | COMPAT | CONFUSABLE | LEETF | DOTWORD | SPACING | SPACED;
const EVASIVE_WORD = ZW | COMPAT | CONFUSABLE | LEETF | PUNCT | SPACED;
const TECH = [[ZW, "zero-width"], [COMPAT, "fullwidth/compat"], [CONFUSABLE, "homoglyph"], [ACCENT, "accents"], [LEETF, "leetspeak"],
  [DOTWORD, "dot-words"], [SPACING, "spacing"], [PUNCT, "punctuation-split"], [SPACED, "spaced-letters"], [WILD, "self-censored"]];
const PLACEHOLDER = "\ufffc";
const KNOWN_SCHEMES = new Set("http https ftp ftps sftp ws wss git ssh irc ircs rtsp mms steam discord tg whatsapp file".split(" "));
const OTHER_SCHEMES = new Set(["mailto", "javascript", "data", "tel", "sms", "magnet", "intent", "market"]);
// Python's [^\W_] / \w / \b are Unicode-aware; JS needs \p{...} spelled out.
const L = "[\\p{L}\\p{N}]";
const W = "[\\p{L}\\p{N}_]";
const B = (inner) => "(?<!" + W + ")" + inner;      // \b before
const BE = "(?!" + W + ")";                         // \b after
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const isAlnum = (c) => /^[\p{L}\p{N}]$/u.test(c);

export function techniques(bits) { return TECH.filter(([b]) => bits & b).map(([, n]) => n); }

export function init(data) {
  D = data;
  TLDS = new Set(D.tlds); FILE_TLDS = new Set(D.file_tlds); RISKY_TLDS = new Set(D.risky_tlds); WORD_TLDS = new Set(D.word_tlds);
  STOPWORDS = new Set(D.stopwords); SHORTENERS = new Set(D.shorteners); CONF = D.confusables; LEET = D.leet;
  ALLOW = D.allow.slice(); ALLOW_SET = new Set(ALLOW);
  RAW = new Set(); ENTRIES = new Map(); PHRASES = [];
  for (const w of D.words) {
    const parts = w.split(/\s+/).filter(Boolean);
    if (parts.length > 1) { PHRASES.push([parts.map((p) => skel(p.normalize("NFKC").toLowerCase())), w]); continue; }
    const s = skel(w.normalize("NFKC").toLowerCase());
    RAW.add(w);
    if (s) { const k = squeeze(s); if (!ENTRIES.has(k)) ENTRIES.set(k, []); ENTRIES.get(k).push([s, w]); }
  }
  STRONG_SQ = D.strong.map((w) => squeeze(skel(w)));
  const risky = [...RISKY_TLDS].sort((a, b) => b.length - a.length).join("|");
  RX = {
    dotword: new RegExp("\\s*(?:[(\\[{<]\\s*(?:dot|d0t|dawt|period|punto|punkt|\\.)\\s*[)\\]}>]|" + B("(?:dot|d0t|dawt)") + BE + ")\\s*", "gu"),
    atword: /\s*[(\[{<]\s*(?:at|@)\s*[)\]}>]\s*/gu,
    schemeDisguise: new RegExp(B("h[x*.\\-_t]{2}p(s?)") + "(?=\\s*:\\s*/\\s*/)", "gu"),
    schemeSpaced: new RegExp(B("(h\\s*t\\s*t\\s*p\\s*s?|f\\s*t\\s*p)") + "\\s*:\\s*/\\s*/\\s*", "gu"),
    spaceDot: new RegExp("(?<=\\S)[ \\t]+\\.[ \\t]*(?=\\S)|(?<=" + L + ")\\.[ \\t]+(?=(" + risky + ")(?!" + L + "))", "gu"),
    spacedRun: new RegExp("(?<!" + L + ")(?:" + L + "[ \\t_*\\-,.]{1,3}){3,}" + L + "+(?!" + L + ")", "gu"),
    singleDots: new RegExp("(?<![\\p{L}\\p{N}.])(?:" + L + "\\.){3,}" + L + "(?!" + L + ")", "gu"),
    md: /\[[^\]\n]{0,300}\]\(\s*[^)\s]*[.:/][^)]*\)/gu,
    html: /<a\b[^>]*>[\s\S]*?<\/a\s*>|<[a-z][^>]*\b(?:href|src)\s*=[^>]*>/gu,
    scheme: new RegExp(B("[a-z][a-z0-9+.\\-]{1,15}://") + "\\S*|" + B("(?:mailto|javascript|data|tel|sms|magnet|intent|market|file):") + "[^\\s]+", "gu"),
    www: new RegExp(B("www\\d{0,3}\\.") + "\\S*", "gu"),
    email: new RegExp("(?<![^\\s(\\[<\"'])[a-z0-9._%+\\-]+@(?:" + L + "(?:[\\p{L}\\p{N}_\\-]{0,61}" + L + ")?\\.)+" + L + "{2,63}" + BE, "gu"),
    ipv4: /(?<![\d.v])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])(?::\d{1,5})?(?:\/\S*)?/gu,
    ipv6: /(?<![0-9a-f:])[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![0-9a-f:])/gu,
    domain: new RegExp("(?<![\\p{L}\\p{N}_@.\\-])((?:" + L + "(?:[\\p{L}\\p{N}_\\-]{0,61}" + L + ")?\\.)+)(" + L + "[\\p{L}\\p{N}_@$\\-]{1,62})(:\\d{1,5})?([/?#][^\\s]*)?", "gu"),
    short: new RegExp(B("(" + [...SHORTENERS].sort().map((s) => { const i = s.indexOf("."); return esc(s.slice(0, i)) + "[\\s._\\-]*" + esc(s.slice(i + 1)); }).join("|") + ")") + "\\s*/\\s*\\S+", "gu"),
  };
}

export async function ready(url) {
  if (D) return true;
  const res = await fetch(url || "/static/filter-data.json?v=2");
  if (!res.ok) throw new Error("filter data");
  init(await res.json());
  return true;
}
export function loaded() { return !!D; }

function invisible(ch) {
  const o = ch.codePointAt(0);
  if ([0x034F, 0x180E, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x17B4, 0x17B5].includes(o)) return true;
  if ((o >= 0xFE00 && o <= 0xFE0F) || (o >= 0xE0100 && o <= 0xE01EF)) return true;
  return /^\p{Cf}$/u.test(ch);
}

class M {
  constructor() { this.c = []; this.s = []; this.f = []; }
  add(ch, span, flag) { this.c.push(ch); this.s.push(span); this.f.push(flag); }
  text() { return this.c.join(""); }
}

export function normalize(text) {
  const m = new M();
  let i = 0;
  for (const ch of text) {
    const span = [i, i + ch.length];
    i += ch.length;
    if (invisible(ch)) { if (m.f.length) m.f[m.f.length - 1] |= ZW; continue; }
    let flag = 0;
    const nf = ch.normalize("NFKC");
    if (nf !== ch && ch.codePointAt(0) > 127) flag |= COMPAT;
    let out = "";
    for (const c2 of nf.normalize("NFD")) { if (/^\p{Mn}$/u.test(c2)) { flag |= ACCENT; continue; } out += c2; }
    for (const c3 of out.toLowerCase()) {
      let rep = CONF[c3];
      let f2 = flag;
      if (rep !== undefined) f2 |= CONFUSABLE; else rep = c3;
      for (let c4 of rep) { if (c4.codePointAt(0) > 0xFFFF) c4 = PLACEHOLDER; m.add(c4, span, f2); }
    }
  }
  return m;
}

function sub(m, rx, fn) {
  const text = m.text();
  const out = new M();
  let pos = 0;
  rx.lastIndex = 0;
  for (const mt of text.matchAll(rx)) {
    const a = mt.index, b = a + mt[0].length;
    if (a === b) continue;
    const r = fn(mt);
    if (r == null || r[0] == null) continue;
    const [rep, flag] = r;
    for (let k = pos; k < a; k++) out.add(m.c[k], m.s[k], m.f[k]);
    const span = [m.s[a][0], m.s[b - 1][1]];
    let fl = flag;
    for (let k = a; k < b; k++) fl |= m.f[k];
    for (const ch of rep) out.add(ch, span, fl);
    pos = b;
  }
  for (let k = pos; k < m.c.length; k++) out.add(m.c[k], m.s[k], m.f[k]);
  return out;
}

const leet = (s) => [...s].map((c) => (LEET[c] !== undefined ? LEET[c] : c)).join("");

function domainOk(m, mt, baseFlags, text) {
  const hostLabels = mt[1].slice(0, -1).split(".").concat([mt[2]]);
  const host = hostLabels.join(".");
  let tld = mt[2];
  const port = mt[3], path = mt[4];
  let extra = 0;
  if (host.includes("_")) return -1;
  if (TLDS.has(tld)) { /* ok */ }
  else if (/[a-z]/.test(tld) && (RISKY_TLDS.has(leet(tld)) || RISKY_TLDS.has(leet(tld.replace(/@/g, "o"))))) {
    extra |= LEETF;
    tld = RISKY_TLDS.has(leet(tld)) ? leet(tld) : leet(tld.replace(/@/g, "o"));
  } else if (tld.startsWith("xn--")) { /* ok */ }
  else return -1;
  const short = SHORTENERS.has(host) || SHORTENERS.has(hostLabels.slice(-2).join("."));
  const strong = !!(port || (path && path.length > 1) || hostLabels.length >= 3 || short);
  if (hostLabels.slice(0, -1).every((x) => [...x].length === 1) && [...tld].length <= 2 && !strong) return -1;
  const t = TLDS.has(tld) ? tld : leet(tld);
  if (!strong && FILE_TLDS.has(t)) return -1;
  if (!strong && WORD_TLDS.has(t)) return -1;
  if (!strong && !RISKY_TLDS.has(t) && hostLabels.length === 2) {
    const s0 = m.s[mt.index + mt[1].length][0];
    const a = text[s0], b = text[s0 + 1];
    if (b !== undefined && /\p{Lu}/u.test(a) && /\p{Ll}/u.test(b)) return -1;
  }
  const flags = baseFlags | extra;
  if (flags & (DOTWORD | SPACING | SPACED)) {
    if (!(RISKY_TLDS.has(t) || short || (path && path.length > 1))) return -1;
    if (STOPWORDS.has(hostLabels[hostLabels.length - 2])) return -1;
  }
  return extra;
}

function hostOf(link) {
  const hm = /(?:\/\/|www\d{0,3}\.|@)([^/\s?#\])>"']+)/u.exec(link);
  return hm ? hm[1].split("@").pop().split(":")[0] : "";
}
function hostOnly(host) {
  host = (host || "").split("@").pop().replace(/^\.+|\.+$/g, "").toLowerCase();
  return host.slice(0, 80);
}

export function findLinks(text) {
  if (typeof text !== "string" || !text || !D) return [];
  let m = normalize(text);
  m = sub(m, RX.schemeDisguise, (mt) => (mt[0] === "http" || mt[0] === "https" ? null : ["http" + mt[1], LEETF]));
  m = sub(m, RX.schemeSpaced, (mt) => [mt[0].replace(/\s+/g, ""), /\s/.test(mt[0]) ? SPACING : 0]);
  m = sub(m, RX.dotword, () => [".", DOTWORD]);
  m = sub(m, RX.atword, () => ["@", DOTWORD]);
  m = sub(m, RX.spaceDot, () => [".", SPACING]);
  m = sub(m, RX.spacedRun, (mt) => {
    const s = mt[0];
    const joined = s.replace(/[ \t_*\-,]+/g, "");
    if (joined === s || !/[ \t_*\-,]/.test(s)) return null;
    return [joined, SPACED];
  });
  m = sub(m, RX.singleDots, (mt) => {
    const labels = mt[0].split(".");
    for (const k of [4, 3, 2]) {
      if (labels.length > k + 1 && RISKY_TLDS.has(labels.slice(-k).join(""))) return [labels.slice(0, -k).join("") + "." + labels.slice(-k).join(""), SPACED];
    }
    return null;
  });
  const t = m.text();
  const hits = [];
  const taken = new Array(t.length).fill(false);
  const free = (a, b) => { for (let k = a; k < b; k++) if (taken[k]) return false; return true; };
  const take = (a, b) => { for (let k = a; k < b; k++) taken[k] = true; };
  const add = (a, b, kind, extra, host) => {
    let fl = extra;
    for (let k = a; k < b; k++) fl |= m.f[k];
    hits.push({start: m.s[a][0], end: m.s[b - 1][1], kind, bits: fl, host});
  };
  for (const [rx, kind] of [[RX.md, "markdown"], [RX.html, "html"], [RX.scheme, "url"], [RX.www, "www"], [RX.email, "email"], [RX.short, "shortener"]]) {
    for (const mt of t.matchAll(rx)) {
      const a = mt.index, b = a + mt[0].length;
      if (a === b || !free(a, b)) continue;
      const host = hostOf(mt[0]);
      let extra = 0;
      if (kind === "url") {
        const sm = /^([a-z][a-z0-9+.\-]*):/.exec(mt[0]);
        if (sm && !KNOWN_SCHEMES.has(sm[1]) && !OTHER_SCHEMES.has(sm[1])) extra = LEETF;
      }
      add(a, b, kind, extra, host);
      take(a, b);
    }
  }
  for (const mt of t.matchAll(RX.ipv4)) {
    const a = mt.index, b = a + mt[0].length;
    if (mt[1].split(".").every((x) => +x <= 255) && free(a, b)) { add(a, b, "ip", 0, mt[1]); take(a, b); }
  }
  for (const mt of t.matchAll(RX.ipv6)) {
    const s = mt[0], a = mt.index, b = a + s.length;
    const colons = (s.match(/:/g) || []).length, dbl = s.split("::").length - 1;
    if ((dbl >= 1 || colons >= 5) && dbl <= 1 && s.length >= 5 && /[0-9a-f]{2}/.test(s) && !/^\d{1,2}(:\d{2}){1,2}$/.test(s) && free(a, b)) {
      add(a, b, "ip", 0, "ipv6"); take(a, b);
    }
  }
  for (const mt of t.matchAll(RX.domain)) {
    const a = mt.index;
    let b = a + mt[0].length;
    if (!free(a, b)) continue;
    let base = 0;
    for (let k = a; k < b; k++) base |= m.f[k];
    const extra = domainOk(m, mt, base, text);
    if (extra < 0) continue;
    while (b > a && ".,;:!?)]}'\"".includes(t[b - 1])) b -= 1;
    add(a, b, "domain", extra, (mt[1] + mt[2]).toLowerCase());
    take(a, b);
  }
  hits.sort((x, y) => x.start - y.start);
  const merged = [];
  for (const h of hits) {
    const last = merged[merged.length - 1];
    if (last && h.start < last.end) { last.end = Math.max(last.end, h.end); last.bits |= h.bits; continue; }
    merged.push(h);
  }
  for (const h of merged) { h.evasion = !!(h.bits & EVASIVE_LINK); h.tech = techniques(h.bits); h.host = hostOnly(h.host); delete h.bits; }
  return merged;
}

export function stripLinks(text) {
  const hits = findLinks(text);
  if (!hits.length) return [text, []];
  let out = "", pos = 0;
  for (const h of hits) { out += text.slice(pos, h.start) + LINK_TEXT; pos = h.end; }
  return [out + text.slice(pos), hits];
}

/* ---------- mature language ---------- */
const TOKCH = /^(?:[\p{L}\p{N}]|[@$!|+])$/u;
const INNER = new Set([".", "-", "_", "*", "'", "\u2019", "~", "#"]);
function runs(s) { const out = []; for (const ch of s) { const l = out[out.length - 1]; if (l && l[0] === ch) l[1] += 1; else out.push([ch, 1]); } return out; }
function squeeze(s) { return runs(s).map((r) => r[0]).join(""); }
function skel(word) { return [...leet(word)].filter(isAlnum).join(""); }
function runsCover(token, entry) {
  const rt = runs(token), re_ = runs(entry);
  if (rt.length !== re_.length) return false;
  return rt.every((a, i) => a[0] === re_[i][0] && a[1] >= re_[i][1]);
}
function lookup(sk) {
  for (const [entry] of ENTRIES.get(squeeze(sk)) || []) if (runsCover(sk, entry)) return entry;
  if (sk.endsWith("s") && sk.length > 3) for (const [entry] of ENTRIES.get(squeeze(sk.slice(0, -1))) || []) if (runsCover(sk.slice(0, -1), entry)) return entry;
  return null;
}
function allowedAt(sk, a, b) {
  for (const w of ALLOW) {
    let start = sk.indexOf(w);
    while (start >= 0) { if (start <= a && b <= start + w.length) return true; start = sk.indexOf(w, start + 1); }
  }
  return false;
}
const COMMON = new Set("it is as a i in on to of he me we us so no do go up at or an by my am be oh ok hi".split(" "));
const PAIR_OK = new Set(["fuck", "shit", "cunt", "bitch", "dick", "cock", "pussy", "whore", "slut", "nigger", "nigga", "faggot", "twat", "wank"]);

function tokens(m) {
  const t = m.text();
  const toks = [];
  let i = 0;
  const n = t.length;
  while (i < n) {
    if (!TOKCH.test(t[i])) { i += 1; continue; }
    let j = i, chars = [], bits = 0, wild = false;
    while (j < n) {
      const ch = t[j];
      if (TOKCH.test(ch)) {
        if (LEET[ch] !== undefined) { chars.push(LEET[ch]); bits |= LEETF; }
        else if (isAlnum(ch)) chars.push(ch);
        else break;
        bits |= m.f[j];
        j += 1;
        continue;
      }
      if (INNER.has(ch) && chars.length) {
        let k = j;
        while (k < n && k - j < 3 && INNER.has(t[k])) k += 1;
        if (k < n && TOKCH.test(t[k])) {
          for (let q = j; q < k; q++) {
            if (t[q] === "*" || t[q] === "#") { wild = true; chars.push("*"); }
            else if (t[q] !== "'" && t[q] !== "\u2019") bits |= PUNCT;
          }
          j = k;
          continue;
        }
      }
      break;
    }
    const word = chars.join("");
    const raw = t.slice(i, j);
    if (/[a-z]/.test(word.replace(/\*/g, "")) && [...raw].some((c) => isAlnum(c) && !/^\p{Nd}$/u.test(c))) {
      if (wild) bits |= WILD;
      toks.push([i, j, word, bits]);
    }
    i = j > i ? j : i + 1;
  }
  for (const tk of toks) {
    const raw = t.slice(tk[0], tk[1]);
    if (![...raw].some((ch) => LEET[ch] !== undefined) || RAW.has(raw) || (raw.endsWith("s") && RAW.has(raw.slice(0, -1)))) tk[3] &= ~LEETF;
  }
  return toks;
}

const EDGE = "@$!|+";
// How many leet symbols (not letters or digits) start and end the token t[a:b].
function edgeSyms(t, a, b) {
  let lead = 0;
  while (a + lead < b && EDGE.includes(t[a + lead])) lead += 1;
  let trail = 0;
  while (b - trail - 1 > a + lead && EDGE.includes(t[b - trail - 1])) trail += 1;
  return [lead, trail];
}

export function findWords(text) {
  if (typeof text !== "string" || !text || !D) return [];
  const m = normalize(text);
  const toks = tokens(m);
  const t = m.text();
  const hits = [];
  const used = new Array(toks.length).fill(false);
  const hit = (i0, i1, entry, bits) => {
    const a = toks[i0][0], b = toks[i1][1];
    if (b <= a) return;
    for (let k = i0; k <= i1; k++) used[k] = true;
    let fl = bits;
    for (let k = a; k < b; k++) fl |= m.f[k];
    hits.push({start: m.s[a][0], end: m.s[b - 1][1], word: entry, bits: fl, mask: entry[0] + MASK.repeat(Math.max(2, entry.length - 1))});
  };
  for (const [parts] of PHRASES) {
    const Ln = parts.length;
    for (let i = 0; i + Ln <= toks.length; i++) {
      if (used.slice(i, i + Ln).some(Boolean)) continue;
      if (parts.every((p, k) => runsCover(toks[i + k][2], p) || toks[i + k][2] === p)) hit(i, i + Ln - 1, parts.join(""), 0);
    }
  }
  let i = 0;
  while (i < toks.length) {
    let j = i;
    while (j < toks.length && toks[j][2].length === 1 && !used[j] && (j === i || /^[\s.\-_*,/|]{1,3}$/u.test(t.slice(toks[j - 1][1], toks[j][0])))) j += 1;
    if (j - i >= 3) {
      const letters = toks.slice(i, j).map((x) => x[2]).join("");
      let done = false;
      for (let size = j - i; size > 2; size--) {
        for (let a = i; a <= j - size; a++) {
          const entry = lookup(letters.slice(a - i, a - i + size));
          if (entry && !used.slice(a, a + size).some(Boolean)) { hit(a, a + size - 1, entry, SPACED); done = true; }
        }
        if (done) break;
      }
      i = j;
    } else i = Math.max(j, i + 1);
  }
  toks.forEach(([a, b, sk, bits], idx) => {
    if (used[idx] || ALLOW_SET.has(sk)) return;
    if (sk.includes("*")) {
      if (!sk.replace(/\*/g, "").length) return;
      const rx = new RegExp("^" + esc(sk).replace(/\\\*/g, "[a-z]") + "s?$");
      let found = null;
      outer: for (const lst of ENTRIES.values()) for (const [entry] of lst) if (entry.length >= 3 && rx.test(entry)) { found = entry; break outer; }
      if (found) hit(idx, idx, found, WILD);
      return;
    }
    const entry = lookup(sk);
    if (entry) { hit(idx, idx, entry, bits); return; }
    // 26.10.13.13: edge leet symbols are usually punctuation ("bitch!" was read as "bitchi"); retry without them.
    const [lead, trail] = edgeSyms(t, a, b);
    if (lead || trail) {
      let found = null, la = 0, tr = 0;
      for ([la, tr] of [[0, trail], [lead, 0], [lead, trail]]) {
        if ((la || tr) && sk.length - la - tr >= 3) {
          const core = sk.slice(la, sk.length - tr);
          if (!ALLOW_SET.has(core)) { found = lookup(core); if (found) break; }
        }
      }
      if (found) { toks[idx] = [a + la, b - tr, sk.slice(la, sk.length - tr), bits]; hit(idx, idx, found, bits); return; }
    }
    const sq = squeeze(sk);
    for (const strong of STRONG_SQ) {
      if (sq.includes(strong) && !allowedAt(sk, sk.indexOf(strong[0]), sk.length) && !ALLOW.some((w) => squeeze(w).includes(strong) && sk.includes(w))) { hit(idx, idx, strong, bits); break; }
    }
  });
  for (let idx = 0; idx < toks.length - 1; idx++) {
    if (used[idx] || used[idx + 1]) continue;
    const a = toks[idx][2], b = toks[idx + 1][2];
    const gap = t.slice(toks[idx][1], toks[idx + 1][0]);
    if (a.length >= 2 && a.length <= 4 && b.length >= 2 && b.length <= 4 && !COMMON.has(a) && !COMMON.has(b) && /^[\s.\-_,]{1,2}$/u.test(gap)) {
      const entry = lookup(a + b);
      if (entry && PAIR_OK.has(entry)) hit(idx, idx + 1, entry, SPACED);
    }
  }
  hits.sort((x, y) => x.start - y.start);
  const out = [];
  for (const h of hits) {
    if (out.length && h.start < out[out.length - 1].end) continue;
    h.evasion = !!(h.bits & EVASIVE_WORD) && !((h.bits & WILD) && !(h.bits & (EVASIVE_WORD & ~PUNCT)));
    h.tech = techniques(h.bits);
    delete h.bits;
    out.push(h);
  }
  return out;
}

export function maskWords(text) {
  const hits = findWords(text);
  if (!hits.length) return [text, []];
  let out = "", pos = 0;
  for (const h of hits) { out += text.slice(pos, h.start) + h.mask; pos = h.end; }
  return [out + text.slice(pos), hits];
}

export function nameProblem(name, links = true) {
  if (typeof name !== "string" || !name.trim() || !D) return null;
  if (links) {
    const lh = findLinks(name);
    if (lh.length) return {kind: "link", evasion: lh.some((h) => h.evasion), tech: [...new Set(lh.flatMap((h) => h.tech))].sort()};
  }
  const wh = findWords(name);
  if (wh.length) return {kind: "language", evasion: wh.some((h) => h.evasion), tech: [...new Set(wh.flatMap((h) => h.tech))].sort()};
  const m = normalize(name);
  const sk = skel([...m.text()].filter((c) => isAlnum(c) || LEET[c] !== undefined).join(""));
  for (const lst of ENTRIES.values()) for (const [entry] of lst) {
    if (entry.length < 4) continue;
    const p = sk.indexOf(entry);
    if (p < 0 || allowedAt(sk, p, p + entry.length)) continue;
    return {kind: "language", evasion: false, tech: []};
  }
  return null;
}

export function filterText(text, links = true, language = true) {
  const report = {links: [], words: []};
  if (links) [text, report.links] = stripLinks(text);
  if (language) [text, report.words] = maskWords(text);
  return [text, report];
}
