/* 26.10.10 "What's new" pop-up, styled like the feature-update email.

   Content: /static/whatsnew/<VERSION>.json, one file per release (tools/release-check.py warns when it is missing).
   Shows once per account per version, after sign-in (this page only loads signed in), when this server's version is
   newer than the last one the account saw. "Seen" lives in localStorage (per account) and on the server
   (/api/account/prefs whatsnew_seen). Accounts made after this version went live on the server ("whatsnew_new")
   never see it; they are marked seen instead. Server name and icon come from /.well-known/branchchat, so it works on
   any self-hosted server. Reopen any time from the menu (#menu-whatsnew). Web only: the Android and desktop apps load
   this page, so they get it with no app change. */

const STAMP = new URL(import.meta.url).searchParams.get("v") || "";
const LS = "bc-whatsnew-seen";

function serverVersion() {
  const el = document.querySelector("#top .ver, header .ver");
  const m = el && /v?(\d[\w.\-]*)/.exec((el.textContent || "").trim());
  return m ? m[1] : "";
}

// "26.10.9.1" < "26.10.10" < "26.11.0-beta.2" < "26.11.0". Returns <0, 0, >0.
export function cmpVersion(a, b) {
  const split = (v) => { const [core, pre] = String(v || "").split("-"); return [core.split(".").map((n) => parseInt(n, 10) || 0), pre || ""]; };
  const [ca, pa] = split(a), [cb, pb] = split(b);
  for (let i = 0; i < Math.max(ca.length, cb.length); i++) { const d = (ca[i] || 0) - (cb[i] || 0); if (d) return d; }
  if (pa === pb) return 0;
  if (!pa) return 1;
  if (!pb) return -1;
  return pa < pb ? -1 : 1;
}

function localSeen(user) { try { return (JSON.parse(localStorage.getItem(LS) || "{}") || {})[user || "_"] || ""; } catch (e) { return ""; } }
function setLocalSeen(user, v) {
  try { const m = JSON.parse(localStorage.getItem(LS) || "{}") || {}; m[user || "_"] = v; localStorage.setItem(LS, JSON.stringify(m)); } catch (e) {}
}
async function getJson(url) {
  const r = await fetch(url, {credentials: "same-origin"});
  if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) throw new Error(String(r.status));
  return r.json();
}
function markSeen(user, v) {
  setLocalSeen(user, v);
  fetch("/api/account/prefs", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({whatsnew_seen: v})}).catch(() => {});
}

let serverInfo = null;
async function info() {
  if (!serverInfo) serverInfo = getJson("/.well-known/branchchat").catch(() => ({}));
  return serverInfo;
}
async function notes(version) { return getJson("/static/whatsnew/" + encodeURIComponent(version) + ".json?v=" + encodeURIComponent(STAMP || version)); }

function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }

let openNow = null;
function show(data, srv, nav) {
  if (openNow) return;
  const prevFocus = document.activeElement;
  const back = el("div", "wn-backdrop");
  const dlg = el("div", "wn-card");
  dlg.setAttribute("role", "dialog"); dlg.setAttribute("aria-modal", "true");
  dlg.setAttribute("aria-labelledby", "wn-title"); dlg.setAttribute("aria-describedby", "wn-intro");
  const x = el("button", "wn-x"); x.type = "button"; x.setAttribute("aria-label", "Close what's new"); x.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const scroll = el("div", "wn-scroll");
  const banner = el("div", "wn-banner"); banner.setAttribute("aria-hidden", "true");
  const img = new Image(); img.src = "/static/email/banner-light.jpg?v=" + encodeURIComponent(STAMP); img.alt = ""; img.decoding = "async";
  img.onerror = () => banner.remove(); banner.append(img);
  const body = el("div", "wn-body");
  body.append(el("p", "wn-kicker", data.kicker || "What's new"));
  const h = el("h2", "wn-title", data.title || "What's new in BranchChat"); h.id = "wn-title"; body.append(h);
  if (srv && srv.name) {
    const who = el("div", "wn-server");
    if (srv.icon) { const ic = new Image(); ic.src = srv.icon + (srv.icon_hash ? "?h=" + encodeURIComponent(srv.icon_hash) : ""); ic.alt = ""; ic.width = 44; ic.height = 44; ic.onerror = () => ic.remove(); who.append(ic); }
    const t = el("div", "wn-server-t"); t.append(el("span", "wn-server-k", "New for you on:"), el("strong", "", srv.name), el("span", "wn-server-h", location.host));
    who.append(t); body.append(who);
  }
  const intro = el("p", "wn-intro", data.intro || ""); intro.id = "wn-intro"; body.append(intro);
  for (const s of data.sections || []) {
    const sec = el("section", "wn-sec");
    sec.append(el("h3", "wn-sec-h", s.heading || ""));
    const ul = el("ul", "wn-list");
    for (const it of s.items || []) {
      const li = el("li", "wn-item");
      const ico = el("span", "wn-ico", it.icon || "•"); ico.setAttribute("aria-hidden", "true");
      const tx = el("div", "wn-tx"); tx.append(el("strong", "", it.title || ""), el("span", "", it.text || ""));
      li.append(ico, tx); ul.append(li);
    }
    sec.append(ul); body.append(sec);
  }
  if (data.apps) { const b = el("div", "wn-apps"); b.append(el("strong", "", "Get the apps"), el("span", "", data.apps)); body.append(b); }
  if (data.thanks) body.append(el("p", "wn-thanks", data.thanks));
  if (data.signoff) { const p = el("p", "wn-sign", data.signoff); if (data.signer) { p.append(document.createElement("br"), el("strong", "", data.signer)); } body.append(p); }
  scroll.append(banner, body);
  const foot = el("div", "wn-foot");
  const ok = el("button", "wn-ok", "Got it"); ok.type = "button";
  const all = el("a", "wn-all", "See all patch notes"); all.href = "/patch-notes";
  foot.append(ok, all);
  // 26.10.12: browse past pop-ups (whatsnew/index.json, oldest first). ←/→ keys and swipe too.
  let step = null;
  if (nav && nav.list.length > 1) {
    const n = nav.list.length, i = nav.i;
    const pv = el("button", "wn-nav", "\u2190"), nx = el("button", "wn-nav", "\u2192");
    pv.type = nx.type = "button"; pv.setAttribute("aria-label", "Older update"); nx.setAttribute("aria-label", "Newer update");
    pv.disabled = i <= 0; nx.disabled = i >= n - 1;
    step = (d) => { const j = i + d; if (j < 0 || j >= n) return; notes(nav.list[j]).then((dd) => { close(); show(dd, srv, {list: nav.list, i: j}); }).catch(() => {}); };
    pv.addEventListener("click", () => step(-1)); nx.addEventListener("click", () => step(1));
    const bar = el("div", "wn-navbar"); bar.style.cssText = "display:flex;align-items:center;justify-content:center;gap:12px;width:100%;margin-top:8px";
    bar.append(pv, el("span", "wn-navlbl", nav.list[i] + " \u00b7 " + (i + 1) + " of " + n), nx); foot.append(bar);
    let sx = null; dlg.addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; }, {passive: true});
    dlg.addEventListener("touchend", (e) => { if (sx == null) return; const dx = e.changedTouches[0].clientX - sx; sx = null; if (Math.abs(dx) > 60) step(dx > 0 ? -1 : 1); }, {passive: true});
  }
  dlg.append(x, scroll, foot);
  back.append(dlg);

  const focusables = () => [...dlg.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")].filter((n) => !n.disabled && n.offsetParent !== null);
  const onKey = (ev) => {
    if (step && (ev.key === "ArrowLeft" || ev.key === "ArrowRight") && !/INPUT|TEXTAREA/.test((ev.target || {}).tagName || "")) { ev.preventDefault(); step(ev.key === "ArrowLeft" ? -1 : 1); return; }
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); return; }
    if (ev.key === "Tab") {
      const f = focusables(); if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (ev.shiftKey && (document.activeElement === first || !dlg.contains(document.activeElement))) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && (document.activeElement === last || !dlg.contains(document.activeElement))) { ev.preventDefault(); first.focus(); }
    }
  };
  const close = () => {
    if (!openNow) return;
    openNow = null;
    document.removeEventListener("keydown", onKey, true);
    document.documentElement.classList.remove("wn-open");
    back.remove();
    if (prevFocus && prevFocus.focus && document.contains(prevFocus)) { try { prevFocus.focus({preventScroll: true}); } catch (e) {} }
  };
  x.addEventListener("click", close);
  ok.addEventListener("click", close);
  back.addEventListener("click", (ev) => { if (ev.target === back) close(); });
  document.addEventListener("keydown", onKey, true);
  document.documentElement.classList.add("wn-open");
  document.body.append(back);
  openNow = close;
  requestAnimationFrame(() => { try { ok.focus({preventScroll: true}); } catch (e) { ok.focus(); } scroll.scrollTop = 0; });
}

async function navFor(v) {
  try { const list = await getJson("/static/whatsnew/index.json?v=" + encodeURIComponent(STAMP || v)); const i = list.indexOf(v); return i < 0 ? null : {list, i}; } catch (e) { return null; }
}

async function open(version) {
  const v = version || serverVersion();
  // 26.10.12: a hotfix (x.x.x.N) has no notes of its own; the menu opens the feature release it fixes.
  const base = /^\d+\.\d+\.\d+\.\d+$/.test(v) ? v.split(".").slice(0, 3).join(".") : "";
  const [data, srv] = await Promise.all([notes(v).catch((e) => { if (base) return notes(base); throw e; }), info()]);
  show(data, srv, await navFor(data.version || v));
}

async function auto() {
  const v = serverVersion();
  if (!v) return;
  let p;
  try { p = await getJson("/api/account/prefs"); } catch (e) { return; }   // offline or older server: stay quiet
  if (!p || !("whatsnew_seen" in p)) return;
  const user = p.whatsnew_user || "";
  if (p.whatsnew_new) { if (cmpVersion(localSeen(user), v) < 0) setLocalSeen(user, v); if (p.whatsnew_seen !== v) markSeen(user, v); return; }
  const seen = [p.whatsnew_seen || "", localSeen(user)].sort(cmpVersion).pop();
  if (seen && cmpVersion(v, seen) <= 0) return;
  let data, shown = v;
  try { data = await notes(v); } catch (e) {
    // 26.10.11.1: a hotfix (four-part version) has no pop-up of its own. Someone who already saw the release it fixes
    // sees nothing; someone who never did gets that release's pop-up once (marked seen as the base version).
    const base = /^\d+\.\d+\.\d+\.\d+$/.test(v) ? v.split(".").slice(0, 3).join(".") : "";
    if (!base || (seen && cmpVersion(base, seen) <= 0)) return;
    try { data = await notes(base); shown = base; } catch (e2) { return; }
  }
  markSeen(user, shown);
  const srv = await info(), nav = await navFor(data.version || shown);
  // Wait for the app's own first-run screens (welcome, keys) to settle.
  const ready = () => !document.querySelector(".wn-backdrop") && !document.querySelector("dialog[open], .modal:not([hidden])");
  let tries = 0;
  const go = () => { if (ready() || tries++ > 20) show(data, srv, nav); else setTimeout(go, 500); };
  setTimeout(go, 1200);
}

function wireMenu() {
  const b = document.getElementById("menu-whatsnew");
  if (!b) return;
  b.addEventListener("click", () => {
    if (typeof window.bcCloseMenu === "function") window.bcCloseMenu();
    open().catch(() => { location.href = "/patch-notes"; });
  });
}

window.bcWhatsNew = {open, cmpVersion};
wireMenu();
if (document.readyState === "complete") auto(); else window.addEventListener("load", () => auto(), {once: true});
