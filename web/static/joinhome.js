/* Branch Chat invite landing on the home server (branchchat.app), 0.23.3.
   Link: /join?s=<server>&n=<name>&k=<lease id, 12 chars>#t=<invite token>
   - The token stays after "#": browsers never send it to branchchat.app. It is sent only to the invited server.
   - Name and icon come from the home's verified listing when there is one, else from the server's own
     /.well-known/branchchat (read straight from that server), else from the link (marked "not verified").
   - The real server address is always shown, and a link whose k does not match the server's lease id is refused.
   - No password is ever typed here. Create account -> <server>/welcome#t=<token>&u=<name>; Sign in -> <server>/#u=<name>. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const USER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
  const q = new URLSearchParams(location.search);
  const frag = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  const KEY = "bc-join-t";
  let token = frag.get("t") || "";
  // Front door (Oct 18): "I have an invite" brings the username chosen there (#u=) and asks to go straight on (#go=create).
  const fdUser = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/.test(frag.get("u") || "") ? frag.get("u") : "";
  const fdGo = frag.get("go") === "create" && !!fdUser;
  try {
    if (token) sessionStorage.setItem(KEY, JSON.stringify({s: q.get("s") || "", t: token}));
    else { const kept = JSON.parse(sessionStorage.getItem(KEY) || "null"); if (kept && kept.s === (q.get("s") || "")) token = kept.t || ""; }
  } catch (err) { /* private mode: keep it in memory */ }
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(token)) token = "";
  const linkName = (q.get("n") || "").replace(/[\u0000-\u001f\u007f<>]/g, "").replace(/\s+/g, " ").trim().slice(0, 48);
  const pin = (q.get("k") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32);

  const st = {origin: "", name: "", icon: "", verified: "", state: "", reach: "", blocked: "", inviter: "", ready: null};

  function alertBox(kind, text, retry) {
    const a = $("alert");
    a.hidden = !text; a.className = "alert " + (kind || "");
    a.textContent = text || "";
    if (retry) { const b = document.createElement("button"); b.type = "button"; b.className = "ghost small"; b.textContent = "Try again"; b.addEventListener("click", () => location.reload()); a.append(" ", b); }
    document.body.dataset.state = kind || "";
  }
  function paint() {
    $("org-name").textContent = st.name || "a BranchChat server";
    document.title = (st.name ? st.name + " · " : "") + "Join on BranchChat";
    if (st.icon) { $("org-icon").src = st.icon; $("hand-icon").src = st.icon; }
    $("hand-name").textContent = st.name || "";
    $("server-host").textContent = st.origin ? st.origin.replace(/^https:\/\//, "") : "(no server in this link)";
    const badge = $("badge");
    badge.textContent = st.verified === "listing" ? "\u2713 verified by the BranchChat directory"
      : st.verified === "server" ? "\u2713 checked with the server" : st.origin ? "not verified: name comes from the link" : "";
    badge.className = "badge " + (st.verified ? "ok" : "warn");
    $("invited-by").hidden = !st.inviter;
    $("invited-by").textContent = st.inviter ? "Invited by " + st.inviter + "." : "";
    $("create").disabled = !!st.blocked || !token;
    $("signin").disabled = st.blocked === "mismatch" || st.blocked === "banned" || !st.origin;
  }
  async function getJSON(url, opts) {
    const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 6000);
    try { const r = await fetch(url, Object.assign({signal: ctl.signal, credentials: "omit", referrerPolicy: "no-referrer"}, opts || {})); return {status: r.status, data: await r.json()}; }
    finally { clearTimeout(timer); }
  }

  async function start() {
    const s = q.get("s") || "";
    if (!s) { alertBox("error", "This invite link is incomplete (no server address). Ask for a new one."); st.blocked = "broken"; paint(); return; }
    let look;
    try { look = (await getJSON("/api/directory/lookup?s=" + encodeURIComponent(s))).data; } catch (err) { look = null; }
    if (!look || !look.ok) { alertBox("error", (look && look.error) || "This invite link is not valid."); st.blocked = "broken"; paint(); return; }
    st.origin = look.origin;
    st.name = linkName;
    if (look.banned) { st.blocked = "banned"; paint(); alertBox("error", "This server was removed from the BranchChat directory. Don't enter anything there unless you trust it."); return; }
    const L = look.listing;
    if (L) {
      if (pin && L.key_fp12 && pin !== L.key_fp12) { st.blocked = "mismatch"; st.name = ""; paint(); alertBox("error", "This link doesn't match the server at " + look.server + ". It may be fake, or the server was reinstalled. Ask for a new invite."); return; }
      // The home doesn't store names or icons; it reads them from the server (cached briefly). When it can't
      // reach the server, this page reads them from the server below.
      if (L.name) st.name = L.name;
      if (L.icon) st.icon = L.icon;
      st.verified = "listing";
    }
    if (L && L.pinned) {   // listed by the directory admin (no lease code on that server): no pre-check to run
      st.reach = "ok";
      if (L.online === false) alertBox("warn", st.name + " didn't answer just now. You can still try; it may be restarting.");
      else if (!token) alertBox("", "");
      paint();
      return;
    }
    if (!token) alertBox("warn", "This link is missing its invite code, so you can only sign in. Ask for the full link to create an account.");
    paint();
    // Pre-check with the server itself (needs the server to allow branchchat.app; it is fine if it doesn't).
    let wk = null;
    try { const r = await getJSON(st.origin + "/.well-known/branchchat"); wk = r.status === 200 ? r.data : null; } catch (err) { wk = null; }
    if (wk && typeof wk === "object") {
      st.reach = "ok";
      const wfp = typeof wk.key_fp === "string" ? wk.key_fp : "";
      if ((pin && wfp.slice(0, 12) !== pin) || (L && L.key_fp12 && wfp.slice(0, 12) !== L.key_fp12)) {
        st.blocked = "mismatch"; st.name = ""; st.icon = ""; $("org-icon").src = "/favicon.png"; paint();
        alertBox("error", "This link doesn't match the server at " + look.server + ". It may be fake, or the server was reinstalled. Ask for a new invite.");
        return;
      }
      const wkName = String(wk.name || "").replace(/[\u0000-\u001f\u007f<>]/g, "").replace(/\s+/g, " ").trim().slice(0, 48);
      if (!L || !L.name) { if (wkName) st.name = wkName; }
      if ((!L || !L.icon) && wk.icon && /^\/[A-Za-z0-9/_-]{1,64}$/.test(wk.icon)) st.icon = st.origin + wk.icon;
      if (!L) st.verified = "server";
      if (token) {
        try {
          const r = await getJSON(st.origin + "/api/invite/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
          const d = r.data || {};
          st.state = d.state || "";
          if (d.state === "valid") { st.inviter = d.invited_by || ""; alertBox("", ""); }
          else if (d.state) {
            st.blocked = d.state;
            const words = {expired: "This invite has expired. Ask for a new one.", used: "This invite was already used. If it was you, sign in.", revoked: "This invite was cancelled. Ask for a new one.", invalid: "This invite code isn't valid on this server. Ask for a new one."};
            alertBox("error", words[d.state] || d.error || "This invite can't be used.");
          }
        } catch (err) { /* CORS not allowed or a blip: the server checks the invite again on its own page */ }
      }
    } else {
      st.reach = "down";
      if (L && !L.online) { st.blocked = "offline"; alertBox("error", st.name + " looks offline right now (it hasn't checked in with the directory). Try again later.", true); }
      else alertBox("warn", "Couldn't reach " + look.server + " to check the invite from here. You can still continue; the server checks it on its own page.");
    }
    paint();
  }

  // Hand-off timing (11:20 PM): "Loading sign-in page" for at least LOADING_MS (or until the pre-check
  // is done, whichever is longer), then the Welcome line for at least WELCOME_MS, then navigate. The pop-up stays
  // up until the browser leaves the page. Lines cross-fade (instant with prefers-reduced-motion), are announced
  // through aria-live, and repeat clicks are ignored while a hand-off is running.
  const LOADING_MS = 800, WELCOME_MS = 2000, FADE_MS = 180;
  let handing = false;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const calm = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (err) { return false; } };
  async function setLine(text) {
    const h = $("hand-title");
    if (h.textContent === text) return;
    if (!calm()) { h.classList.add("fading"); await wait(FADE_MS); }
    h.textContent = text;
    h.dataset.shownAt = String(Date.now());
    h.classList.remove("fading");
  }
  async function go(mode) {
    if (handing) return;
    const user = $("username").value.trim();
    $("msg").textContent = "";
    $("username").removeAttribute("aria-invalid");
    if (!USER_RE.test(user)) {
      $("msg").textContent = user.length < 3 || user.length > 32 ? "Usernames are 3\u201332 characters." : "Use letters, numbers, dots, dashes or underscores, starting with a letter or number.";
      $("username").setAttribute("aria-invalid", "true"); $("username").focus(); return;
    }
    if (mode === "create" && (st.blocked || !token)) return;
    handing = true;
    document.querySelectorAll("#join-form button").forEach((b) => b.setAttribute("aria-disabled", "true"));
    const h = $("hand-title");
    h.classList.remove("fading");
    h.textContent = "";
    $("hand-host").textContent = st.origin.replace(/^https:\/\//, "");
    $("hand").hidden = false;
    document.body.classList.add("handing");
    h.textContent = "Loading sign-in page";   // set after the pop-up is visible so aria-live announces it
    h.dataset.shownAt = String(Date.now());
    await Promise.all([Promise.resolve(st.ready), wait(LOADING_MS)]);
    if (mode === "create" && st.blocked) {   // the pre-check found a problem: show it instead of leaving
      $("hand").hidden = true; document.body.classList.remove("handing"); handing = false;
      document.querySelectorAll("#join-form button").forEach((b) => b.removeAttribute("aria-disabled"));
      return;
    }
    await setLine("Welcome to BranchChat. You're being redirected to " + (st.name || st.origin.replace(/^https?:\/\//, "")));
    const target = mode === "create"
      ? st.origin + "/welcome#t=" + encodeURIComponent(token) + "&u=" + encodeURIComponent(user)
      : st.origin + "/#u=" + encodeURIComponent(user);
    try { sessionStorage.removeItem(KEY); } catch (err) { /* ignore */ }
    await wait(WELCOME_MS);
    window.__bcHandoffAt = Date.now();
    location.assign(target);   // the pop-up is never hidden here; it goes away with the page
  }
  $("join-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const mode = (ev.submitter && ev.submitter.dataset.mode) || "create";
    go(mode);
  });
  // Another invite opened in this tab with the same server differs only after "#": start over with it.
  window.addEventListener("hashchange", () => location.reload());
  st.ready = start();
  if (fdUser) $("username").value = fdUser;
  // Straight to the hand-off pop-up when the invite checks out; otherwise the page shows why and waits.
  if (fdGo) Promise.resolve(st.ready).then(() => { if (token && !st.blocked) go("create"); });
})();
