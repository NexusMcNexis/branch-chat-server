/* Branch Chat directory page (home server). Browse -> highlight a server -> Continue -> hand-off pop-up -> its
   sign-in page. Everything is drawn with textContent: names are never HTML. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const list = $("list"), go = $("dir-continue");
  let picked = null, handing = false;
  // Front door (Oct 18): "Find my family or group" arrives as /directory?u=<username>. Picking a server then hands off
  // to that server's own sign-up page with the username filled in (/welcome#u=...; it asks for the invite link there).
  const fdUser = (function () { const u = new URLSearchParams(location.search).get("u") || ""; return /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/.test(u) ? u : ""; })();
  if (fdUser) {
    $("dir-how").textContent = "Find your family or group, pick it, then Continue to create your account there as " + fdUser + ".";
    history.replaceState(null, "", location.pathname);
  }
  function li(cls, text) { const el = document.createElement("li"); el.className = cls; el.textContent = text; return el; }
  // The directory stores no names or icons for leased servers. When branchchat.app couldn't read them from a
  // server just now, read them from that server's public info here (only name + icon path are used).
  function fillFromServer(s, nm, img) {
    nm.firstChild.textContent = s.server;
    const origin = s.login_url.replace(/\/$/, "");
    // 26.10.3: give up after 5 seconds. A listed server that only answers on its home network (like a test server)
    // used to keep this request, and the page's loading spinner, hanging until the browser gave up minutes later.
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const stop = ctl ? setTimeout(() => ctl.abort(), 5000) : 0;
    fetch(origin + "/.well-known/branchchat", {credentials: "omit", referrerPolicy: "no-referrer", signal: ctl ? ctl.signal : undefined})
      .finally(() => clearTimeout(stop)).then((r) => r.json()).then((wk) => {
      if (!wk || typeof wk.key_fp !== "string" || wk.key_fp.slice(0, 12) !== s.key_fp12) return;
      const name = String(wk.name || "").replace(/[\u0000-\u001f\u007f<>]/g, "").replace(/\s+/g, " ").trim().slice(0, 48);
      if (name) { nm.firstChild.textContent = name; s.name = name; }
      if (wk.type === "family" && !s.type) { s.type = "family"; const t = document.createElement("span"); t.className = "dir-tag"; t.textContent = "Family"; nm.append(t); }
      if (typeof wk.icon === "string" && /^\/[A-Za-z0-9/_-]{1,64}$/.test(wk.icon)) { img.src = origin + wk.icon; s.icon = img.src; }
    }).catch(() => { /* not reachable from here either: the address stays as the name */ });
  }
  function select(item, s) {
    list.querySelectorAll('[role="option"]').forEach((o) => { o.setAttribute("aria-selected", "false"); o.tabIndex = -1; });
    item.setAttribute("aria-selected", "true"); item.tabIndex = 0; picked = s;
    go.disabled = false; go.textContent = "Continue to " + (s.name || s.server);
  }
  // Same hand-off as the invite landing: "Loading sign-in page" >= 800 ms, then the Welcome line >= 2000 ms,
  // cross-fade (none with reduced motion), aria-live, repeat clicks ignored, pop-up stays until the page leaves.
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const calm = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (err) { return false; } };
  async function handOff() {
    if (handing || !picked) return;
    if (!/^https:\/\//.test(picked.login_url)) return;
    handing = true; go.setAttribute("aria-disabled", "true");
    const s = picked, h = $("hand-title"), name = s.name || s.server;
    $("hand-icon").src = s.icon || "/favicon.png"; $("hand-name").textContent = name; $("hand-host").textContent = s.server;
    h.classList.remove("fading"); h.textContent = "";
    $("hand").hidden = false; document.body.classList.add("handing");
    h.textContent = "Loading sign-in page";
    await wait(800);
    if (!calm()) { h.classList.add("fading"); await wait(180); }
    h.textContent = "Welcome to BranchChat. You're being redirected to " + name;
    h.classList.remove("fading");
    await wait(2000);
    location.assign(fdUser ? s.login_url.replace(/^(https:\/\/[^/]+).*$/, "$1") + "/welcome#u=" + encodeURIComponent(fdUser) : s.login_url);
  }
  go.addEventListener("click", handOff);
  // Search: filters the list by name or address as you type.
  const search = $("dir-search");
  if (search) search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    list.querySelectorAll(".dir-item").forEach((el) => {
      const hit = !q || el.textContent.toLowerCase().includes(q);
      el.hidden = !hit; if (hit) shown++;
    });
    let none = list.querySelector(".dir-none");
    if (!shown && list.querySelector(".dir-item")) { if (!none) { none = li("dir-empty dir-none", "No listed server matches that."); list.append(none); } }
    else if (none) none.remove();
  });
  list.addEventListener("keydown", (ev) => {
    const opts = Array.from(list.querySelectorAll('[role="option"]'));
    const i = opts.indexOf(document.activeElement);
    if (i < 0) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); const n = opts[(i + (ev.key === "ArrowDown" ? 1 : opts.length - 1)) % opts.length]; n.focus(); n.click(); }
    else if (ev.key === "Enter") { ev.preventDefault(); if (picked) handOff(); }
    else if (ev.key === " ") { ev.preventDefault(); document.activeElement.click(); }
  });
  fetch("/api/directory", {credentials: "omit"}).then((r) => r.json()).then((d) => {
    list.textContent = "";
    if (d.ttl) $("ttl").textContent = Math.round(d.ttl / 60) + " minutes";
    const rows = Array.isArray(d.servers) ? d.servers : [];
    if (!rows.length) { list.append(li("dir-empty", "No servers are online in the directory right now.")); return; }
    rows.forEach((s, idx) => {
      const item = document.createElement("li"); item.className = "dir-item"; item.dataset.server = s.server;
      item.setAttribute("role", "option"); item.setAttribute("aria-selected", "false"); item.tabIndex = idx === 0 ? 0 : -1;
      const ic = document.createElement("span"); ic.className = "org-icon";
      const img = document.createElement("img"); img.alt = ""; img.width = 48; img.height = 48; img.src = s.icon || "/favicon.png";
      ic.append(img);
      const txt = document.createElement("div"); txt.className = "dir-text";
      const nm = document.createElement("p"); nm.className = "dir-name"; nm.append(document.createTextNode(s.name || s.server));
      if (s.type === "family" || s.type === "public") { const t = document.createElement("span"); t.className = "dir-tag"; t.textContent = s.type === "public" ? "Public" : "Family"; nm.append(t); }
      const host = document.createElement("p"); host.className = "dir-host";
      const on = s.online !== false;
      const dot = document.createElement("span"); dot.className = "dot" + (on ? "" : " off"); dot.setAttribute("aria-label", on ? "online" : "offline");
      host.append(dot, document.createTextNode(" " + s.server + (on ? "" : " \u00b7 offline right now")));
      txt.append(nm, host);
      const pick = document.createElement("span"); pick.className = "dir-pick"; pick.setAttribute("aria-hidden", "true");
      item.append(ic, txt, pick); list.append(item);
      item.addEventListener("click", () => select(item, s));
      item.addEventListener("dblclick", () => { select(item, s); handOff(); });
      if (s.info_from_server && /^https?:\/\//.test(s.login_url)) fillFromServer(s, nm, img);
    });
  }).catch(() => { list.textContent = ""; list.append(li("dir-empty", "The directory couldn't load. Try again in a moment.")); });
})();
