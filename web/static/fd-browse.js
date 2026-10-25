/* directory-signin (Oct 21 2026, Luke): on the web front door, "Sign in here" (web; the apps keep "Sign in on web") first opens the Browse groups pop-up
   (the same /api/directory list as /directory). Picking this server (the Public server) opens the sign-in form here;
   picking another server shows the usual hand-off and goes to that server's own sign-in page. "Use my own server"
   is at the bottom. Browsers only: the apps keep the old sign-in (frontdoor.py does not load this file there).
   Everything is drawn with textContent: names are never HTML. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const dlg = $("fd-browse"), btn = $("gate-signin"), list = $("fdb-list"), search = $("fdb-search");
  if (!dlg || !btn || !list) return;
  let loaded = false, handing = false;
  const clean = (v) => String(v || "").replace(/[\u0000-\u001f\u007f<>]/g, "").replace(/\s+/g, " ").trim().slice(0, 48);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  function close() { if (dlg.open) dlg.close(); }
  function openLogin() {
    close();
    // fd-gate.js toggles the login form on its own gate-signin handler; ask it directly.
    $("signup").hidden = true; $("fd-pick").hidden = true;
    $("su-to-login").click();
    btn.setAttribute("aria-expanded", "true");
  }
  async function go(s) {
    if (s.self || new URL(s.login_url, location.href).host === location.host) { openLogin(); return; }
    if (handing || !/^https:\/\//.test(s.login_url)) return;
    handing = true; close();
    const name = s.name || s.server;
    $("hand-icon").src = s.icon || "/favicon.png"; $("hand-name").textContent = name; $("hand-host").textContent = s.server;
    $("fd-hand").hidden = false; document.body.classList.add("handing");
    $("hand-title").textContent = "Loading sign-in page";
    await wait(800);
    $("hand-title").textContent = "Welcome to BranchChat. You're being redirected to " + name + ".";
    await wait(1500);
    location.assign(s.login_url);
  }
  function row(s) {
    const li = document.createElement("li");
    const b = document.createElement("button"); b.type = "button"; b.className = "fdb-item"; b.dataset.server = s.server;
    const img = document.createElement("img"); img.alt = ""; img.width = 40; img.height = 40; img.src = s.icon || "/favicon.png";
    const txt = document.createElement("span"); txt.className = "fdb-text";
    const nm = document.createElement("b"); nm.textContent = clean(s.name) || s.server;
    if (s.type === "public" || s.type === "family") { const t = document.createElement("span"); t.className = "fdb-tag"; t.textContent = s.type === "public" ? "Public" : "Family"; nm.append(t); }
    const host = document.createElement("small"); const on = s.online !== false;
    host.textContent = (on ? "\u25cf " : "\u25cb ") + s.server + (on ? "" : " \u00b7 offline right now");
    host.className = on ? "on" : "off";
    txt.append(nm, host); b.append(img, txt); li.append(b);
    b.addEventListener("click", () => go(s));
    return li;
  }
  function note(text) { list.textContent = ""; const li = document.createElement("li"); li.className = "fdb-empty"; li.textContent = text; list.append(li); }
  function load() {
    if (loaded) return; loaded = true;
    note("Loading groups\u2026");
    fetch("/api/directory", {credentials: "omit"}).then((r) => r.json()).then((d) => {
      const rows = Array.isArray(d.servers) ? d.servers : [];
      list.textContent = "";
      if (!rows.some((s) => s.self)) rows.unshift({self: true, name: "BranchChat Public server", server: location.host, login_url: "/?login", icon: "/favicon.png", type: "public", online: true});
      rows.forEach((s) => list.append(row(s)));
    }).catch(() => { loaded = false; note("The group list couldn't load. You can still sign in here."); list.append(row({self: true, name: "BranchChat Public server", server: location.host, login_url: "/?login", icon: "/favicon.png", type: "public"})); });
  }
  if (search) search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    list.querySelectorAll(".fdb-item").forEach((el) => { el.parentElement.hidden = !!q && !el.textContent.toLowerCase().includes(q); });
  });
  // Capture phase: runs before fd-gate.js's own toggle, which is skipped while the pop-up handles the click.
  btn.addEventListener("click", (ev) => {
    if (!$("login").hidden) return;           // already showing the form: let fd-gate.js fold it
    ev.stopImmediatePropagation();
    load(); if (search) search.value = ""; list.querySelectorAll("li").forEach((li) => { li.hidden = false; });
    dlg.showModal();
  }, true);
  $("fdb-own").addEventListener("click", () => { close(); $("fd-own").click(); });
  $("fdb-close").addEventListener("click", close);
  dlg.addEventListener("click", (ev) => { if (ev.target === dlg) close(); });
})();
