/* Invite acceptance. Creating the account signs this browser in immediately.
   The login code runs on later sign-ins, not on this first visit.
   0.21.07: the form is novalidate and checked here, so a short password or a mismatch always says so in #msg
   (the browser's own minlength check could block Create account without showing anything, e.g. in Firefox).
   The rules match the server (accounts.USERNAME_RE, passwords 8-200 characters). */
const secureOn = window.isSecureContext;
document.getElementById("secure-led").classList.toggle("on", secureOn);
document.getElementById("secure-text").textContent = secureOn ? "Secure connection" : "Not a secure connection";

const PASS_MIN = 8, PASS_MAX = 200;
const USER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
// Invite without email: same simple shape check as the server's; the server also refuses an email another account uses.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const params = new URLSearchParams(location.search);
/* 0.23.3: new links carry the token (and, from the branchchat.app landing page, the chosen username) after "#",
   so they never reach any server log: /welcome#t=<token>&u=<username>. Old ?token= links still work. */
const frag = new URLSearchParams((location.hash || "").replace(/^#/, ""));
const token = frag.get("t") || params.get("token") || "";
const $ = (id) => document.getElementById(id);
(function prefill() {
  const u = (frag.get("u") || "").trim();
  if (u && USER_RE.test(u)) { $("username").value = u; }
  if (frag.get("t") || frag.get("u")) history.replaceState(null, "", location.pathname + (params.get("token") ? location.search : ""));
  if (u && USER_RE.test(u)) setTimeout(() => $("password").focus(), 0);
})();
// 26.10.2: a cancelled invite gets its own clear page instead of a sign-up form that can't work: the form fields
// are hidden and a short "Invite cancelled" notice says what happened and what to do. The server refuses the link
// anyway (accept_invite), so this is only about explaining it.
function showCancelled() {
  document.body.dataset.invite = "revoked";
  const form = $("welcome");
  for (const node of [...form.children]) { if (!node.classList.contains("brand")) node.hidden = true; }
  const box = document.createElement("div");
  box.id = "invite-cancelled";
  box.className = "invite-gone";
  const h = document.createElement("h2"); h.textContent = "Invite cancelled";
  const p = document.createElement("p"); p.textContent = "The person who sent this invite, or an admin, cancelled it, so this link no longer works. Ask them for a new invite.";
  const a = document.createElement("a"); a.href = "/"; a.textContent = "Go to the sign-in page";
  box.append(h, p, a);
  form.append(box);
}
(async function checkInvite() {
  if (!token) { $("msg").textContent = "This page needs the invite link from your email or message."; return; }
  try {
    const res = await fetch("/api/invite/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
    const data = await res.json();
    if (data && data.state && data.state === "revoked") {
      showCancelled();
    } else if (data && data.state && data.state !== "valid") {
      $("msg").textContent = data.error || "That link is not valid.";
      document.body.dataset.invite = data.state;
      $("welcome").querySelector("button[type=submit]").disabled = true;
    } else if (data) {
      const lead = document.getElementById("invited-by");
      if (lead && data.invited_by) lead.textContent = "Invited by " + data.invited_by + ".";
      // Invite without email: an older invite sent by email fills the email in (the person can still change it).
      if (data.email && !$("email").value) $("email").value = data.email;
    }
  } catch (err) { /* the server still checks the link when the form is sent */ }
})();
const fields = ["username", "email", "password", "again"];

// Remove the red "invalid" marks from every field.
function clearInvalid() { for (const id of fields) $(id).removeAttribute("aria-invalid"); }
// Show an error, mark the field it is about and focus it. Returns false.
function fail(id, text) {
  clearInvalid();
  $("msg").textContent = text;
  $(id).setAttribute("aria-invalid", "true");
  $(id).focus();
  return false;
}
// Check username and password rules before creating the account from an invite.
function checkForm() {
  const username = $("username").value.trim();
  const password = $("password").value;
  const email = $("email").value.trim();
  if (username.length < 3 || username.length > 32) return fail("username", "Username must be 3\u201332 characters.");
  if (!USER_RE.test(username)) return fail("username", "Usernames can use letters, numbers, dots, dashes and underscores, and must start with a letter or number.");
  if (!email) return fail("email", "Enter your email address.");
  if (email.length > 200 || !EMAIL_RE.test(email)) return fail("email", "Enter a real email address, like name@example.com.");
  if (password.length < PASS_MIN) return fail("password", "Password must be at least 8 characters.");
  if (password.length > PASS_MAX) return fail("password", "Password can be at most 200 characters.");
  if (password !== $("again").value) return fail("again", "Passwords don't match.");
  clearInvalid();
  return true;
}
// Live password length hint under the password field.
function paintCount() {
  // live hint under the password field (the rule itself is always shown)
  const n = $("password").value.length;
  $("password-count").textContent = n === 0 ? "" : n < PASS_MIN ? " " + n + " so far, " + (PASS_MIN - n) + " to go." : n > PASS_MAX ? " That is " + (n - PASS_MAX) + " too many." : " \u2713";
  $("password-hint").classList.toggle("is-ok", n >= PASS_MIN && n <= PASS_MAX);
}
for (const id of fields) $(id).addEventListener("input", () => { if ($(id).getAttribute("aria-invalid")) { $(id).removeAttribute("aria-invalid"); $("msg").textContent = ""; } });
$("password").addEventListener("input", paintCount);

let busy = false;
/* Recovery file removed: make this browser's message keys now (or reuse the ones it already has) and save
   them in the server's password-sealed key backup, the same backup sign-in on a new device unlocks with the
   email code. If anything here fails the account still works: the app makes keys on first load and the next
   sign-in on this browser saves the backup. */
async function backupKeys(password) {
  const {createIdentity, sealRecovery} = await import("/static/crypto.js?v=2");
  const db = await new Promise((resolve, reject) => {
    const req = indexedDB.open("huzz-chat", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const kv = (mode, fn) => new Promise((resolve, reject) => {
    const req = fn(db.transaction("kv", mode).objectStore("kv"));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  let stored = await kv("readonly", (st) => st.get("device"));
  // 26.10.2 fix: a brand-new account always gets its own fresh message key. A key already in this browser belongs to
  // another account made or used here before; reusing it sealed that account's key into this one's backup and made the
  // server refuse the device, so the new account's bar said "Account". The old key is set aside (not deleted) under
  // "device-<its id>" so the other account finds it again next time it signs in here.
  if (stored && stored.deviceId && stored.privateKey) {
    const old = stored;
    await kv("readwrite", (st) => st.put(old, "device-" + old.deviceId));
  }
  stored = await createIdentity();
  await kv("readwrite", (st) => st.put(stored, "device"));
  const packed = JSON.parse(await sealRecovery(stored.deviceId, stored.privateKey, stored.publicKey, password));
  await fetch("/api/account/key-backup", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({salt: packed.salt, iv: packed.iv, ct: packed.ct}),
  });
}
$("welcome").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (busy) return;
  const msg = $("msg");
  msg.textContent = "";
  if (!checkForm()) return;
  const username = $("username").value.trim();
  const password = $("password").value;
  const email = $("email").value.trim();
  busy = true;
  const button = $("welcome").querySelector("button[type=submit]");
  button.disabled = true;
  msg.textContent = "Creating your account\u2026";
  let res;
  try {
    res = await fetch("/api/welcome", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token, username, email, password}),
    });
  } catch (err) {
    msg.textContent = "The page could not reach BranchChat. Check the connection and try again.";
    busy = false; button.disabled = false;
    return;
  }
  let data = {};
  try { data = await res.json(); } catch (err) { data = {}; }
  if (!res.ok) {
    msg.textContent = data.error || "That link was not accepted.";
    busy = false; button.disabled = false;
    return;
  }
  $("password").value = "";
  $("again").value = "";
  try { await backupKeys(password); } catch (err) { /* the app makes keys; the next sign-in saves the backup */ }
  if (offerApp()) return;
  location.replace("/");
});

/* 26.10.10: signed up in a phone browser on Android (not inside the app): offer "Open in BranchChat app". A tap asks
   this server for a 2-minute single-use sign-in token and opens <home>/app-login?s=<this server>#h=<token>, a link the
   app verifies for the home address only, so it works for every server. Without the app that page sends the browser
   back here. "Continue in browser" just goes on. */
function offerApp() {
  if (!/Android/i.test(navigator.userAgent || "") || window.BranchChatApp) return false;
  const form = $("welcome");
  for (const el of Array.from(form.children)) if (!el.classList.contains("brand")) el.style.display = "none";   // gate.css shows labels even when [hidden]
  const box = document.createElement("div");
  box.id = "open-app";
  box.innerHTML = '<p class="ok">Your account is ready.</p><p>Have the BranchChat app? Open it there and you\u2019ll be signed in.</p>' +
    '<button type="button" id="open-app-go">Open in BranchChat app</button>' +
    '<button type="button" class="ghost" id="open-app-skip">Continue in browser</button><div class="msg" id="open-app-msg" role="status"></div>';
  form.append(box);
  $("open-app-skip").addEventListener("click", () => location.replace("/"));
  $("open-app-go").addEventListener("click", async () => {
    const b = $("open-app-go");
    b.disabled = true;
    $("open-app-msg").textContent = "";
    try {
      const r = await fetch("/api/handoff/create", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
      const d = await r.json();
      if (!r.ok || !d.token || !d.home || !d.server) throw new Error(d.error || "");
      const link = d.home + "/app-login?s=" + encodeURIComponent(d.server) + "#h=" + encodeURIComponent(d.token);
      if (new URL(d.home).origin !== location.origin) { location.assign(link); return; }
      // On the home itself Chrome keeps same-site links in the tab, so ask for the app by package. The link rides in
      // an intent extra (never a request); without the app Chrome goes to the fallback (this server's start page).
      const u = new URL(link);
      location.assign("intent://" + u.host + u.pathname + u.search + "#Intent;scheme=https;package=app.branchchat.android;S.bc_link=" +
        encodeURIComponent(link) + ";S.browser_fallback_url=" + encodeURIComponent(location.origin + "/") + ";end");
    } catch (err) {
      $("open-app-msg").textContent = err.message || "Couldn't open the app. Open BranchChat and sign in there.";
      b.disabled = false;
    }
  });
  return true;
}
