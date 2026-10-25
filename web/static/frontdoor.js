/* Branch Chat front door (branchchat.app first screen), Oct 18 2026.
   Views: Create account (step 1 username, step 2 email/password/13+), Log in, Use my own server.
   login.js (password, authenticator, email code, reset) is unchanged and loads after this file.
   Rules match the server: usernames 3-32 letters, numbers, dots, dashes, underscores; passwords 8-200. */
const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const USER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const RETURNING = "bc-returning";
const signup = $("signup"), login = $("login"), own = $("own");
const later = ["factor", "reset", "unlock"].map($).filter(Boolean);
const signupOpen = root.dataset.signup === "1";
const coarse = (() => { try { return matchMedia("(pointer: coarse)").matches; } catch (err) { return false; } })();

// Focus a field after the view changes, but not on touch screens (it would pop the keyboard up).
function focusSoon(el) { if (el && !coarse) setTimeout(() => el.focus(), 0); }
// Switch the front door between its views (choices, Log in, Sign up, Your own server); sign-up only when open.
function show(view) {
  if (view === "signup" && !signupOpen) view = "login";
  signup.hidden = view !== "signup";
  login.hidden = view !== "login";
  own.hidden = view !== "own";
  for (const f of later) f.hidden = true;
  root.dataset.fdView = view;
  focusSoon(view === "signup" ? (signup.dataset.step === "2" ? $("su-email") : $("su-username")) : view === "login" ? $("username") : $("own-addr"));
}
// login.js shows its later steps (authenticator, email code, reset) by un-hiding them: keep the view marker right.
new MutationObserver(() => {
  const step = later.find((f) => !f.hidden);
  const v = step ? "step" : (!login.hidden ? "login" : !own.hidden ? "own" : "signup");
  if (root.dataset.fdView !== v) root.dataset.fdView = v;
}).observe(document.body, {subtree: true, attributes: true, attributeFilter: ["hidden"]});

$("su-to-login").addEventListener("click", () => show("login"));
$("to-signup").addEventListener("click", () => show("signup"));
let before = "signup";
$("fd-own").addEventListener("click", (ev) => { ev.preventDefault(); if (own.hidden) before = login.hidden ? "signup" : "login"; show("own"); });
$("own-back").addEventListener("click", () => show(before));

let first = "signup";
try { if (localStorage.getItem(RETURNING) === "1") first = "login"; } catch (err) { /* private mode */ }
if (/^#u=/.test(location.hash || "") || new URLSearchParams(location.search).has("login")) first = "login";
if (location.hash === "#own") first = "own";
show(first);

/* ---------- Create account ---------- */
const siteKey = (document.querySelector('meta[name="bc-turnstile"]') || {}).content || "";
let tsToken = "", tsWidget = null;
// Show the Cloudflare Turnstile check on the sign-up form, once, when the server has a site key.
function startTurnstile() {
  if (!siteKey || tsWidget !== null) return;
  const box = $("su-turnstile");
  box.hidden = false;
  const go = () => {
    if (!window.turnstile) return false;
    tsWidget = window.turnstile.render(box, {
      sitekey: siteKey, theme: root.dataset.scheme === "light" ? "light" : "dark",
      callback: (t) => { tsToken = t; }, "expired-callback": () => { tsToken = ""; }, "error-callback": () => { tsToken = ""; },
    });
    return true;
  };
  if (!go()) { const timer = setInterval(() => { if (go()) clearInterval(timer); }, 300); setTimeout(() => clearInterval(timer), 20000); }
}
const fields = ["su-username", "su-email", "su-password", "su-again", "su-agree", "su-invite-link"];
// Remove the red "invalid" marks from every sign-up field.
function clearInvalid() { for (const id of fields) $(id).removeAttribute("aria-invalid"); }
// Show a sign-up error, mark the field it is about and put the cursor there. Returns false for the caller.
function fail(id, text) {
  clearInvalid();
  $("su-error").textContent = text;
  if (id && $(id)) { $(id).setAttribute("aria-invalid", "true"); $(id).focus(); }
  return false;
}
for (const id of fields) $(id).addEventListener("input", () => { if ($(id).getAttribute("aria-invalid")) { $(id).removeAttribute("aria-invalid"); $("su-error").textContent = ""; } });

/* Steps: 1 username -> choose (invite / find my family or group / public server) -> invite link | 2 (public sign-up). */
function step(n) {
  n = String(n);
  signup.dataset.step = n;
  root.dataset.fdStep = n;
  $("su-step1").hidden = n !== "1";
  $("su-choose").hidden = n !== "choose";
  $("su-invite").hidden = n !== "invite";
  $("su-step2").hidden = n !== "2";
  $("su-go").hidden = n === "choose";
  $("su-go").textContent = n === "invite" ? "Continue" : "Create account";
  $("su-title").textContent = n === "2" ? "Create your account on " + $("fd-server").querySelector("b").textContent : "Create your account";
  $("su-error").textContent = "";
  const u = $("su-username").value.trim();
  for (const id of ["su-who", "su-who1", "su-who2"]) $(id).textContent = u;
  if (n === "2") { startTurnstile(); focusSoon($("su-email")); }
  else if (n === "choose") focusSoon($("opt-invite"));
  else if (n === "invite") focusSoon($("su-invite-link"));
  else focusSoon($("su-username"));
}
for (const id of ["su-change", "su-change1", "su-change2"]) $(id).addEventListener("click", () => step(1));
$("su-invite-back").addEventListener("click", () => step("choose"));
$("opt-invite").addEventListener("click", () => step("invite"));
$("opt-find").addEventListener("click", () => {
  // The directory search; picking a server there shows the hand-off pop-up to that server's sign-up page.
  location.assign("/directory?u=" + encodeURIComponent($("su-username").value.trim()));
});
$("opt-public").addEventListener("click", async () => {
  if (busy) return;
  const b = $("opt-public"); b.setAttribute("aria-busy", "true"); busy = true;
  const {res, data} = await post("/api/signup/check", {username: $("su-username").value.trim()});
  busy = false; b.removeAttribute("aria-busy");
  if (!res) { $("su-error").textContent = "The page could not reach BranchChat. Check the connection and try again."; return; }
  if (!res.ok) { step(1); fail("su-username", data.error || "That username can't be used."); return; }
  step(2);
});
/* "Try the public Lobby" (front page entry point): same as Create account -> Use the public server. */
let lobbyIntent = false;
// "Join the public Lobby": open sign-up with the Lobby wording (the account joins the Lobby after).
function startLobby() {
  lobbyIntent = true;
  show("signup"); step(1);
  $("su-title").textContent = "Join the public Lobby";
}
$("fd-lobby").addEventListener("click", (ev) => { ev.preventDefault(); startLobby(); });
$("su-to-login").addEventListener("click", () => { lobbyIntent = false; $("su-title").textContent = "Create your account"; });
if (location.hash === "#lobby" && signupOpen) startLobby();
window.addEventListener("hashchange", () => { if (location.hash === "#lobby" && signupOpen) startLobby(); });
// Arrived on the front door with an invite (?s=...#t=...): that is the invite landing's job, no choose step.
(function inviteArrival() {
  const q = new URLSearchParams(location.search);
  if (q.get("s") && /(^|[#&])t=/.test(location.hash || "")) location.replace("/join" + location.search + location.hash);
})();

// POST JSON and return {res, data}; a network failure comes back as res null instead of throwing.
async function post(url, body) {
  let res = null, data = {};
  try {
    res = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    try { data = await res.json(); } catch (err) { data = {}; }
  } catch (err) { res = null; }
  return {res, data};
}
let busy = false;
// Disable the Create account button while a request runs, with a label saying what is happening.
function setBusy(on, label) {
  busy = on;
  const b = $("su-go");
  b.disabled = on;
  b.textContent = label || "Create account";
}
signup.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (busy) return;
  $("su-error").textContent = "";
  const u = $("su-username").value.trim();
  if (signup.dataset.step === "1") {
    if (u.length < 3 || u.length > 32) return fail("su-username", "Username must be 3\u201332 characters.");
    if (!USER_RE.test(u)) return fail("su-username", "Usernames can use letters, numbers, dots, dashes and underscores, and must start with a letter or number.");
    if (lobbyIntent) { $("opt-public").click(); return; }   // "Try the public Lobby": the public server, no choose step
    step("choose");
    return;
  }
  if (signup.dataset.step === "choose") return;
  if (signup.dataset.step === "invite") { useInvite(u); return; }
  const e = $("su-email").value.trim(), p = $("su-password").value;
  if (!EMAIL_RE.test(e) || e.length > 200) return fail("su-email", "Enter a real email address.");
  if (p.length < 8) return fail("su-password", "Password must be at least 8 characters.");
  if (p.length > 200) return fail("su-password", "Password can be at most 200 characters.");
  if (p !== $("su-again").value) return fail("su-again", "Passwords don't match.");
  if (!$("su-agree").checked) return fail("su-agree", "Tick the box to agree before creating an account.");
  if (siteKey && !tsToken) return fail("", "Finish the human check first.");
  clearInvalid();
  setBusy(true, "Creating your account\u2026");
  const {res, data} = await post("/api/signup", {username: u, email: e, password: p, agree: true, turnstile: tsToken});
  if (!res || !res.ok) {
    setBusy(false);
    if (data.field === "username") step(1);
    const map = {username: "su-username", email: "su-email", password: "su-password", agree: "su-agree"};
    fail(map[data.field] || "", res ? (data.error || "That sign-up was not accepted.") : "The page could not reach BranchChat. Check the connection and try again.");
    if (tsWidget !== null && window.turnstile) { try { window.turnstile.reset(tsWidget); } catch (err) { /* ignore */ } tsToken = ""; }
    return;
  }
  try { localStorage.setItem(RETURNING, "1"); } catch (err) { /* ignore */ }
  $("su-password").value = ""; $("su-again").value = "";
  location.replace("/" + location.search.replace(/([?&])view=app(&|$)/, "$1").replace(/[?&]$/, ""));
});
login.addEventListener("submit", () => { try { localStorage.setItem(RETURNING, "1"); } catch (err) { /* ignore */ } });

/* ---------- Use my own server: check it, then the hand-off pop-up ---------- */
const LOADING_MS = 800, WELCOME_MS = 2000;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const clean = (s) => String(s || "").replace(/[\u0000-\u001f\u007f<>]/g, "").replace(/\s+/g, " ").trim().slice(0, 48);
// Fetch JSON from another server with a 6 second timeout, without cookies or referrer.
async function getJSON(url) {
  const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(url, {signal: ctl.signal, credentials: "omit", referrerPolicy: "no-referrer"});
    return {status: r.status, data: await r.json()};
  } finally { clearTimeout(timer); }
}
// Show an error on the "your own server" address field.
function ownFail(text) {
  $("own-error").textContent = text;
  $("own-addr").setAttribute("aria-invalid", "true");
  $("own-addr").focus();
}
$("own-addr").addEventListener("input", () => { $("own-error").textContent = ""; $("own-addr").removeAttribute("aria-invalid"); });
let handing = false;
// Show which server you are going to (name, address, icon), then send you to its sign-in page.
async function handOff(st, target) {
  const host = st.origin.replace(/^https:\/\//, "");
  $("hand-name").textContent = st.name || host;
  $("hand-host").textContent = host;
  if (st.icon) $("hand-icon").src = st.icon;
  $("hand-title").textContent = "";
  $("fd-hand").hidden = false;
  document.body.classList.add("handing");
  $("hand-title").textContent = "Loading sign-in page";
  await wait(LOADING_MS);
  $("hand-title").textContent = "Welcome to BranchChat. You're being redirected to " + (st.name || host) + ".";
  await wait(WELCOME_MS);
  window.__bcHandoffAt = Date.now();
  location.assign(target);   // the pop-up goes away with the page
}
// A server's public info (/.well-known/branchchat), or null when it isn't a Branch Chat server.
async function serverInfo(origin) {
  try { const r = await getJSON(origin + "/.well-known/branchchat"); return r.status === 200 && r.data && typeof r.data === "object" ? r.data : null; }
  catch (err) { return null; }
}
/* "I have an invite": a branchchat.app-style link (/join?s=...#t=...) goes through the invite landing, which checks it
   and shows the hand-off pop-up straight away (the chosen username rides along after "#"). A server's own
   /welcome#t=... link is checked here and handed off the same way. */
function inviteFail(text) { fail("su-invite-link", text); }
// "I have an invite": check the pasted link and go to the server and page it belongs to.
async function useInvite(user) {
  const raw = $("su-invite-link").value.trim();
  if (!raw) return inviteFail("Paste the invite link.");
  let url;
  try { url = new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw); } catch (err) { url = null; }
  if (!url || !url.hostname.includes(".")) return inviteFail("That's only part of an invite. Paste the whole link, starting with https://");
  const frag = new URLSearchParams((url.hash || "").replace(/^#/, ""));
  const token = frag.get("t") || url.searchParams.get("token") || "";
  if (url.pathname === "/join" && url.searchParams.get("s")) {
    const h = new URLSearchParams();
    if (token) h.set("t", token);
    h.set("u", user); h.set("go", "create");
    location.assign("/join" + url.search + "#" + h.toString());
    return;
  }
  if (url.pathname === "/welcome" && token) {
    setBusy(true, "Checking\u2026");
    const wk = await serverInfo(url.origin);
    if (!wk) { setBusy(false, "Continue"); return inviteFail("Couldn't reach the server in that link (" + url.host + "). Check the link and try again."); }
    const st = {origin: url.origin, name: clean(wk.name), icon: typeof wk.icon === "string" && /^\/[A-Za-z0-9/_-]{1,64}$/.test(wk.icon) ? url.origin + wk.icon : ""};
    handOff(st, url.origin + "/welcome#t=" + encodeURIComponent(token) + "&u=" + encodeURIComponent(user));
    return;
  }
  inviteFail("That doesn't look like a BranchChat invite link.");
}
own.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (handing) return;
  let raw = $("own-addr").value.trim().replace(/\/+$/, "");
  if (!raw) return ownFail("Type the server's address, like chat.example.org.");
  if (!/^https?:\/\//i.test(raw)) raw = "https://" + raw;
  let url;
  try { url = new URL(raw); } catch (err) { return ownFail("That doesn't look like a server address."); }
  if (!/^[a-z0-9.-]+$/i.test(url.hostname) || !url.hostname.includes(".") && url.hostname !== "localhost") return ownFail("That doesn't look like a server address.");
  if (url.origin === location.origin) { show(before); return; }
  handing = true;
  const go = $("own-go");
  go.disabled = true; go.textContent = "Checking\u2026";
  const st = {origin: url.origin, name: "", icon: ""};
  let listed = false;
  try {
    const look = (await getJSON("/api/directory/lookup?s=" + encodeURIComponent(url.host))).data;
    if (look && look.ok) {
      if (look.banned) { handing = false; go.disabled = false; go.textContent = "Continue"; return ownFail("That server was removed from the BranchChat directory."); }
      if (look.origin) st.origin = look.origin;
      if (look.listing) { listed = true; st.name = clean(look.listing.name); st.icon = look.listing.icon || ""; }
    }
  } catch (err) { /* not a home server, or a blip: ask the server itself */ }
  const wk = await serverInfo(st.origin);
  if (!wk && !listed) {
    handing = false; go.disabled = false; go.textContent = "Continue";
    return ownFail("Couldn't find a BranchChat server at " + url.host + ". Check the address and try again.");
  }
  if (wk) {
    if (!st.name) st.name = clean(wk.name);
    if (!st.icon && typeof wk.icon === "string" && /^\/[A-Za-z0-9/_-]{1,64}$/.test(wk.icon)) st.icon = st.origin + wk.icon;
  }
  handOff(st, st.origin + "/");
});
