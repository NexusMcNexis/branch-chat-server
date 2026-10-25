/* Phone alerts for Branch Chat.
   The server sends a wake-up only. This worker reads the new ciphertext
   and decrypts it with the key already on this phone. */

import {decryptBytes, decryptJson, unwrap} from "/static/crypto.js?v=2";

const ICON = "/static/icons/icon-192.png";
const BADGE = "/static/icons/badge-96.png";

function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("huzz-chat", 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains("kv")) req.result.createObjectStore("kv");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbGet(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const req = db.transaction("kv").objectStore("kv").get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function idbSet(key, value) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const req = db.transaction("kv", "readwrite").objectStore("kv").put(value, key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function raiseSeq(seq) {
  if (!seq) return;
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("kv", "readwrite");
    const store = tx.objectStore("kv");
    const req = store.get("notify-seq");
    req.onsuccess = () => {
      const current = Number(req.result) || 0;
      if (seq > current) store.put(seq, "notify-seq");
      resolve();
    };
    req.onerror = () => reject(req.error);
  });
}

/* Wake-ups carry no message text. This worker reads ciphertext with the
   session cookie and decrypts it with the device key. A deleted message
   has no ciphertext, so it is not announced.
*/
function muted(raw) {
  if (!raw) return false;
  if (raw === "until") return true;
  const match = /^(\d+):(\d+)$/.exec(String(raw));
  return !!(match && Number(match[2]) > Date.now());
}

// 0.23.2: per-chat mute, stored on the server ({chat key: until}, 0 = until turned back on). It comes with /api/sync.
function chatMuted(mutes, key) {
  if (!mutes || !key) return false;
  const k = key.indexOf("dm:") === 0 ? "dm:" + key.slice(3).toLowerCase() : key;
  if (!Object.prototype.hasOwnProperty.call(mutes, k)) return false;
  const until = Number(mutes[k]);
  return until === 0 || until * 1000 > Date.now();
}
function clean(text) {
  return String(text || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
}

function preview(payload, msg) {
  const text = clean(payload && payload.text);
  const file = payload && payload.file;
  let extra = "";
  if (file && file.kind === "gif") extra = "GIF";
  else if (file && file.kind === "image") extra = "Photo";
  else if (file && file.kind === "video") extra = "Video";
  else if (file || (msg && msg.blob)) extra = "Attachment";
  let body = text;
  if (text && extra) body = text + " (" + extra + ")";
  else if (extra) body = extra;
  if (!body) body = "New message";
  if (body.length > 180) body = body.slice(0, 177) + "...";
  return body;
}

function chatKey(msg, me) {
  if (!msg || msg.event || msg.sender === me) return "";
  if (msg.box === "lobby") return "lobby";
  if (msg.box === "group" && msg.g) return "g:" + msg.g;   // 0.23.0 group chats
  if (msg.box === "dm" && msg.sender) return "dm:" + msg.sender;
  return "";
}

async function groupKeyOf(groups, gid, epoch, device) {
  const g = (groups || []).find((item) => item.id === gid);
  const wrap = g && (g.wraps || []).find((item) => item.epoch === epoch && item.device_id === device.deviceId);
  if (!wrap) return null;
  const key = await unwrap(device.privateKey, wrap);
  return key && key.length === 32 ? key : null;
}

async function groupTitle(groups, gid, device) {
  // The group name is encrypted with the group key. Without it the alert just says "Group".
  try {
    const g = (groups || []).find((item) => item.id === gid);
    const meta = g && g.meta;
    if (!meta || !meta.ct || !device || !device.privateKey) return "Group";
    const key = await groupKeyOf(groups, gid, meta.epoch, device);
    if (!key) return "Group";
    const value = await decryptJson(key, meta.iv, meta.ct);
    return clean(value && value.name).slice(0, 48) || "Group";
  } catch (err) {
    return "Group";
  }
}

async function openOne(msg, device, wraps, groups) {
  let contentKey;
  if (msg.box === "group") {
    const groupKey = await groupKeyOf(groups, msg.g, msg.epoch, device);
    if (!groupKey) return null;
    contentKey = await decryptBytes(groupKey, msg.epoch_iv, msg.epoch_ct);
    return decryptJson(contentKey, msg.iv, msg.ct);
  }
  if (msg.box === "lobby") {
    const wrap = (wraps || []).find((item) => item.epoch === msg.epoch && item.device_id === device.deviceId);
    if (!wrap) return null;
    const epochKey = await unwrap(device.privateKey, wrap);
    if (!epochKey || epochKey.length !== 32) return null;
    contentKey = await decryptBytes(epochKey, msg.epoch_iv, msg.epoch_ct);
  } else {
    const wrap = (msg.wraps || []).find((item) => item.device_id === device.deviceId);
    if (!wrap) return null;
    contentKey = await unwrap(device.privateKey, wrap);
  }
  return decryptJson(contentKey, msg.iv, msg.ct);
}

async function focusedOn(key) {
  const clients = await self.clients.matchAll({type: "window", includeUncontrolled: true});
  const visible = clients.some((client) => client.focused && client.visibilityState === "visible");
  if (!visible) return false;
  const focus = await idbGet("notify-focus");
  return !!(focus && focus.key === key);
}

async function avatarIcon(sender) {
  if (!sender) return ICON;
  try {
    const res = await fetch("/api/avatar?u=" + encodeURIComponent(sender), {credentials: "same-origin"});
    if (!res.ok) return ICON;
    const bytes = new Uint8Array(await res.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return "data:image/jpeg;base64," + btoa(bin);
  } catch (err) {
    return ICON;
  }
}

let pushShown = 0;   // notifications shown, for the Safari no-silent-push rule below

async function showOne(title, body, key, sender) {
  pushShown += 1;
  const options = {
    body: body,
    icon: await avatarIcon(sender),
    badge: BADGE,
    tag: "branch-" + key,
    data: {key: key},
  };
  try {
    await self.registration.showNotification(title, options);
  } catch (err) {
    await self.registration.showNotification(title, {body: body, icon: ICON, badge: BADGE, tag: "branch-" + key, data: {key: key}});
  }
}

async function onPush() {
  if (muted(await idbGet("notify-mute"))) return;
  const device = await idbGet("device");
  let cursor = Number(await idbGet("notify-seq")) || 0;
  const armed = await idbGet("notify-armed");
  let me = "";
  let lobby = {joined: false, wraps: []};
  let groups = [];
  let mutes = {};
  const fresh = [];
  let guard = 0;
  let since = cursor;
  let more = true;
  while (more && guard < 5) {
    const res = await fetch("/api/sync?since=" + since + "&react=0", {credentials: "same-origin"});
    if (res.status === 401) {
      await showOne("BranchChat", "Sign in to read a new message.", "signin");
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    me = data.me && data.me.username || me;
    lobby = data.lobby || lobby;
    groups = data.groups || groups;
    mutes = data.mutes || mutes;
    const batch = data.messages || [];
    for (const msg of batch) fresh.push(msg);
    if (!batch.length) break;
    since = batch[batch.length - 1].seq;
    more = !!data.more;
    guard += 1;
  }
  let maxSeq = cursor;
  const latest = new Map();
  for (const msg of fresh) {
    if (msg.seq > maxSeq) maxSeq = msg.seq;
    if (!armed && msg.ts && (Date.now() / 1000 - msg.ts) > 90) continue;
    // A removal has no text. Do not announce it as a new message.
    if (msg.deleted) continue;
    const key = chatKey(msg, me);
    if (!key) continue;
    if (key === "lobby" && !(lobby && lobby.joined)) continue;
    if (key.indexOf("g:") === 0 && !groups.some((g) => "g:" + g.id === key)) continue;
    if (chatMuted(mutes, key)) continue;   // muted chat: no alert (the app still counts it as unread)
    latest.set(key, msg);
  }
  for (const [key, msg] of latest) {
    if (await focusedOn(key)) continue;
    const many = key === "lobby" || key.indexOf("g:") === 0;
    const title = key === "lobby" ? "Lobby" : key.indexOf("g:") === 0 ? await groupTitle(groups, msg.g, device) : (msg.sender || "BranchChat");
    let body = many ? (msg.sender || "Someone") + " sent a message" : "New message";
    if (device && device.privateKey) {
      try {
        const payload = await openOne(msg, device, lobby.wraps || [], groups);
        if (payload) {
          const text = preview(payload, msg);
          body = many ? (msg.sender || "Someone") + ": " + text : text;
        }
      } catch (err) { /* show the sender without the text */ }
    }
    await showOne(title, body, key, msg.sender);
  }
  if (maxSeq > cursor) await raiseSeq(maxSeq);
  await idbSet("notify-armed", 1);
}

/* 26.10.6: offline app shell + a fetch handler, so Chromium/Android treats BranchChat as installable
   (no fetch handler = "Add shortcut" only) and so the app can grow real offline support later.
   Rules: only same-origin GETs are touched. /api/*, auth, push, uploads, /sw.js and the manifest go straight to
   the network, never cached. Pages (navigations) are network-first; if the network is down the offline page is
   shown. Signed-in page HTML is never stored (it carries your name). /static/ files are cache-first: every page
   asks for them with a ?v= version, so a new deploy asks for new URLs and the old copies are dropped with the
   old cache on activate. Bump SHELL_V with the version; /sw.js itself is served no-store and registered with
   updateViaCache "none", so a deploy still replaces this worker (skipWaiting + clients.claim below). */
const SHELL_V = "{{BC_SHELL}}";   // filled in by server.py when serving: VERSION + a fingerprint of the pages, so a page change always refreshes the cache
const SHELL_CACHE = "bc-shell-" + SHELL_V;
const STATIC_CACHE = "bc-static-" + SHELL_V;
const OFFLINE_URL = "/offline.html";
const SHELL = [OFFLINE_URL, "/instance.css", "/static/offline.css?v=1", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];
const NEVER = /^\/(api|auth|login|logout|push|upload|uploads|blob|ws|sw\.js|manifest\.webmanifest|share|\.well-known)(\/|$|\?)/;

self.addEventListener("install", (event) => {
  self.skipWaiting();
  // A missing shell file must not stop the worker installing (push alerts depend on it): cache what we can.
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) =>
    Promise.all(SHELL.map((url) => cache.add(new Request(url, {cache: "reload"})).catch(() => null)))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keep = [SHELL_CACHE, STATIC_CACHE];
    for (const name of await caches.keys()) {
      if ((name.indexOf("bc-shell-") === 0 || name.indexOf("bc-static-") === 0) && keep.indexOf(name) < 0) {
        await caches.delete(name);
      }
    }
    await self.clients.claim();
  })());
});

async function offlinePage() {
  const hit = await caches.match(OFFLINE_URL, {cacheName: SHELL_CACHE});
  return hit || new Response("<!doctype html><title>BranchChat is offline</title><p>BranchChat is offline.</p>",
    {status: 503, headers: {"Content-Type": "text/html; charset=utf-8"}});
}

async function instanceCss(request) {
  try {
    const res = await fetch(request);
    if (res && res.ok) caches.open(SHELL_CACHE).then((c) => c.put("/instance.css", res.clone())).catch(() => {});
    return res;
  } catch (err) {
    return (await caches.match("/instance.css", {cacheName: SHELL_CACHE})) ||
      new Response("", {status: 200, headers: {"Content-Type": "text/css; charset=utf-8"}});
  }
}

async function staticFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res && res.ok && res.type === "basic" && !request.headers.has("range")) {
    cache.put(request, res.clone()).catch(() => {});
  }
  return res;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;                       // POSTs (login, send, upload): browser default
  let url;
  try { url = new URL(request.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;            // YouTube, Turnstile, other servers: untouched
  if (NEVER.test(url.pathname)) return;                       // API, auth, push, manifest, the worker itself
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => offlinePage()));
    return;
  }
  if (url.pathname === "/instance.css") {
    // Per-server colours, linked from every page (offline page too). Network-first; offline, use the copy cached at
    // install, else an empty sheet. A failed stylesheet load here made WebKit log CSP "inline stylesheet" refusals.
    event.respondWith(instanceCss(request));
    return;
  }
  if (url.pathname.indexOf("/static/") === 0 && !request.headers.has("range")) {
    event.respondWith(staticFirst(request).catch(async () =>
      (await caches.match(request)) || Response.error()));
  }
});

/* iPhone/iPad (and Mac Safari) web push: every push must show a notification. A push that shows nothing (you were
   reading that chat, the chat is muted, or the message was a removal) counts as "silent", and after a few of those
   Safari quietly cancels the subscription, so alerts stop until the app is reopened. When nothing was shown, Safari
   gets a silent placeholder that is closed straight away. Other browsers allow quiet pushes and are left alone. */
const SAFARI_PUSH = /Safari\//.test(self.navigator.userAgent) && !/Chrome|Chromium|CriOS|Edg|Firefox|FxiOS|Android/.test(self.navigator.userAgent);

async function quietPlaceholder() {
  await self.registration.showNotification("BranchChat", {body: "You're up to date.", tag: "bc-quiet", silent: true, badge: BADGE, icon: ICON});
  const open = await self.registration.getNotifications({tag: "bc-quiet"});
  for (const n of open) n.close();
}

self.addEventListener("push", (event) => {
  const before = pushShown;
  event.waitUntil(onPush().catch(() => showOne("BranchChat", "New message", "error"))
    .then(() => { if (SAFARI_PUSH && pushShown === before) return quietPlaceholder(); })
    .catch(() => {}));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const key = (event.notification.data && event.notification.data.key) || "";
  event.waitUntil(openRoom(key));
});

async function openRoom(key) {
  const all = await self.clients.matchAll({type: "window", includeUncontrolled: true});
  for (const client of all) {
    if (client.url.indexOf(self.location.origin) !== 0) continue;
    await client.focus();
    client.postMessage({type: "open", key: key});
    return;
  }
  await self.clients.openWindow("/?open=" + encodeURIComponent(key || "lobby"));
}
