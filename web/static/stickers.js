/* stickers.js (26.10.4): BranchChat stickers.
   - The packs are this server's own pictures in /static/stickers/, listed in /static/stickers/manifest.json.
   - Sending a sticker sends an ordinary end-to-end encrypted message. Its payload carries
     {sticker: {pack, id, alt}} plus a plain text line "[Sticker: <alt>]". Apps that don't know stickers yet
     (older web pages, older phone apps) simply show that text line, so nothing breaks.
   - Only names travel in the message. The picture always comes from this server's own manifest, never from a
     link inside a message, so a message can't point the app at some other picture.
   - "Recently used" is kept per account: on this device (localStorage, one key per account) and on the server
     (/api/account/stickers), so it follows the account to other devices.
   app.js calls stickerInit() once with the few app functions this file needs. */

const NAME_OK = /^[a-z0-9][a-z0-9-]{0,47}$/;
const RECENT_MAX = 16;

const S = {
  deps: null,            // app helpers handed over by app.js
  manifest: null,        // the parsed manifest (null until loaded)
  byKey: new Map(),      // "pack/id" -> {pack, sticker}
  loading: null,
  recent: [],            // newest first, "pack/id"
  recentFor: "",         // the account the recent list belongs to
  saveTimer: 0,
  query: "",
};

function reduceMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

export function stickerInit(deps) {
  S.deps = deps;
  loadManifest();
}

// Fetch the manifest once. When it arrives, app.js re-paints the chat so stickers already on screen show their pictures.
export function loadManifest() {
  if (S.loading) return S.loading;
  S.loading = fetch("/static/stickers/manifest.json?v=" + encodeURIComponent(S.deps && S.deps.version || "1"), {credentials: "same-origin"})
    .then((r) => (r.ok ? r.json() : null))
    .then((m) => {
      if (!m || !Array.isArray(m.packs)) return null;
      S.manifest = m;
      S.byKey.clear();
      for (const pack of m.packs) {
        if (!pack || !NAME_OK.test(pack.id || "") || !Array.isArray(pack.stickers)) continue;
        for (const st of pack.stickers) if (st && NAME_OK.test(st.id || "")) S.byKey.set(pack.id + "/" + st.id, {pack, sticker: st});
      }
      if (S.deps && S.deps.onReady) S.deps.onReady();
      return m;
    })
    .catch(() => { S.loading = null; return null; });
  return S.loading;
}

// The sticker this message payload names, if this server has it: {pack, sticker} or null.
export function stickerOf(payload) {
  const st = payload && payload.sticker;
  if (!st || typeof st !== "object") return null;
  const pack = String(st.pack || ""), id = String(st.id || "");
  if (!NAME_OK.test(pack) || !NAME_OK.test(id)) return null;
  return S.byKey.get(pack + "/" + id) || null;
}
export function isStickerPayload(payload) { return !!(payload && payload.sticker && typeof payload.sticker === "object"); }

// The words to show when the picture can't be: the sender's alt text, or ours.
export function stickerAlt(payload) {
  const hit = stickerOf(payload);
  const alt = hit ? hit.sticker.alt : String((payload && payload.sticker && payload.sticker.alt) || "").slice(0, 120);
  return alt || "Sticker";
}

// The picture for a sticker message. Animated stickers play their animated file unless the device asks for less
// motion; still stickers with a "motion" hint get a short, gentle CSS movement when they first appear.
const played = new Set();   // messages whose sticker already did its little movement (the chat is re-drawn often)
export function stickerElement(payload, msgId) {
  const hit = stickerOf(payload);
  if (!hit) return null;
  const {sticker} = hit;
  const img = document.createElement("img");
  img.className = "sticker-img";
  img.alt = "Sticker: " + sticker.alt;
  img.title = sticker.alt;
  img.decoding = "async";
  img.draggable = false;
  img.width = 512; img.height = 512;   // reserves the square before the picture arrives, so the chat doesn't jump
  const still = "/static/stickers/" + sticker.file;
  img.src = sticker.anim && !reduceMotion() ? "/static/stickers/" + sticker.anim : still;
  // The movement plays once per message per visit, not again every time the chat is re-drawn.
  if (sticker.motion && !sticker.anim && !reduceMotion() && !(msgId && played.has(msgId))) {
    img.classList.add("st-m-" + sticker.motion);
    if (msgId) played.add(msgId);
  }
  img.addEventListener("error", () => {   // missing picture: fall back to the words
    const t = document.createElement("span");
    t.className = "sticker-alt";
    t.textContent = "[Sticker: " + sticker.alt + "]";
    img.replaceWith(t);
  }, {once: true});
  return img;
}

// What goes in the encrypted message. "text" is the fallback older apps show.
export function stickerPayload(packId, id) {
  const hit = S.byKey.get(packId + "/" + id);
  if (!hit) return null;
  return {text: "[Sticker: " + hit.sticker.alt + "]", file: null, sticker: {pack: packId, id, alt: hit.sticker.alt, v: 1}};
}

/* ---- recently used, per account ---- */
function recentKey() { return "bc-sticker-recent:" + (S.recentFor || ""); }
function recentLoad() {
  const me = S.deps && S.deps.me ? S.deps.me() : "";
  if (S.recentFor === me) return;
  S.recentFor = me;
  S.recent = [];
  try {
    const saved = JSON.parse(localStorage.getItem(recentKey()) || "[]");
    if (Array.isArray(saved)) S.recent = saved.filter((k) => typeof k === "string").slice(0, RECENT_MAX);
  } catch (err) { S.recent = []; }
  // Pull the account's list from the server too; the newest-first union wins.
  if (me && S.deps.api) S.deps.api("/api/account/stickers").then((d) => {
    if (S.recentFor !== me || !d || !Array.isArray(d.recent)) return;
    const merged = [...S.recent];
    for (const k of d.recent) if (!merged.includes(k)) merged.push(k);
    S.recent = merged.slice(0, RECENT_MAX);
    try { localStorage.setItem(recentKey(), JSON.stringify(S.recent)); } catch (err) {}
    if (paneShowing()) fillStickerPane(S.query);
  }).catch(() => {});
}
function recentUse(key) {
  S.recent = [key, ...S.recent.filter((k) => k !== key)].slice(0, RECENT_MAX);
  try { localStorage.setItem(recentKey(), JSON.stringify(S.recent)); } catch (err) {}
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => {   // a quick burst of stickers is saved to the server once
    if (S.deps.post) S.deps.post("/api/account/stickers", {recent: S.recent}).catch(() => {});
  }, 1500);
}

/* ---- the Stickers tab ---- */
function paneShowing() { const p = document.getElementById("sticker-pop"); return !!(p && !p.hidden); }

function tileFor(packId, sticker) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "sticker-tile";
  b.dataset.key = packId + "/" + sticker.id;
  b.setAttribute("aria-label", "Send sticker: " + sticker.alt);
  b.title = sticker.alt;
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.width = 512; img.height = 512;
  img.src = "/static/stickers/" + (sticker.thumb || sticker.file);
  b.append(img);
  return b;
}
function head(text, id) {
  const h = document.createElement("div");
  h.className = "sticker-head";
  h.textContent = text;
  if (id) h.id = id;
  return h;
}
function matches(pack, sticker, words) {
  if (!words.length) return true;
  const hay = (sticker.alt + " " + (sticker.tags || []).join(" ") + " " + pack.name).toLowerCase();
  return words.every((w) => hay.includes(w));
}

// Fill (or refill, after a search) the Stickers tab: pack shortcuts, Recently used, then each pack.
export function fillStickerPane(query) {
  const pane = document.getElementById("sticker-pop");
  if (!pane) return;
  S.query = String(query || "");
  recentLoad();
  if (!S.manifest) {
    pane.replaceChildren(head("Loading stickers…"));
    loadManifest().then((m) => { if (paneShowing()) fillStickerPane(S.query); if (!m) pane.replaceChildren(head("Stickers could not be loaded. Try again in a moment.")); });
    return;
  }
  const words = S.query.toLowerCase().split(/\s+/).filter(Boolean);
  const frag = document.createDocumentFragment();
  // Shortcut row: one small picture per pack; tapping it scrolls to that pack. Hidden while searching.
  if (!words.length) {
    const bar = document.createElement("div");
    bar.className = "sticker-packs";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Sticker packs");
    for (const pack of S.manifest.packs) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.jump = pack.id;
      b.title = pack.name;
      b.setAttribute("aria-label", pack.name);
      const i = document.createElement("img");
      i.alt = ""; i.src = "/static/stickers/" + pack.icon; i.width = 64; i.height = 64; i.loading = "lazy";
      b.append(i);
      bar.append(b);
    }
    frag.append(bar);
    const recent = S.recent.map((k) => [k, S.byKey.get(k)]).filter(([, v]) => v);
    if (recent.length) {
      frag.append(head("Recently used"));
      const g = document.createElement("div");
      g.className = "sticker-grid sticker-recent";
      for (const [, v] of recent) g.append(tileFor(v.pack.id, v.sticker));
      frag.append(g);
    }
  }
  let any = false;
  for (const pack of S.manifest.packs) {
    const list = pack.stickers.filter((st) => matches(pack, st, words));
    if (!list.length) continue;
    any = true;
    frag.append(head(pack.name, "sticker-pack-" + pack.id));
    const g = document.createElement("div");
    g.className = "sticker-grid";
    for (const st of list) g.append(tileFor(pack.id, st));
    frag.append(g);
    if (pack.credit && !words.length) {
      const c = document.createElement("p");
      c.className = "sticker-credit";
      c.textContent = pack.credit;
      frag.append(c);
    }
  }
  if (!any) frag.append(head("No stickers matched. Try another word."));
  pane.replaceChildren(frag);
}

// One listener for the whole pane (tiles come and go with every search).
export function wireStickerPane() {
  const pane = document.getElementById("sticker-pop");
  if (!pane || pane.dataset.wired) return;
  pane.dataset.wired = "1";
  pane.addEventListener("click", async (ev) => {
    const jump = ev.target.closest("[data-jump]");
    if (jump) {
      const h = document.getElementById("sticker-pack-" + jump.dataset.jump);
      if (h) pane.scrollTo({top: h.offsetTop - pane.offsetTop - 52, behavior: reduceMotion() ? "auto" : "smooth"});
      return;
    }
    const tile = ev.target.closest(".sticker-tile");
    if (!tile || tile.disabled) return;
    const [packId, id] = tile.dataset.key.split("/");
    const payload = stickerPayload(packId, id);
    if (!payload) return;
    tile.disabled = true;   // one tap, one sticker
    try {
      await S.deps.send(payload);
      recentUse(packId + "/" + id);
    } catch (err) {
      if (S.deps.note) S.deps.note(err.message || "That sticker could not be sent.");
    } finally {
      tile.disabled = false;
    }
  });
}
