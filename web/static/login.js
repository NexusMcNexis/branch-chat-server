/* Sign-in for a browser.

   has_keys is true only when IndexedDB already has this device's private
   key. Password comes first. When the account turned on an authenticator
   app, that code is next and the session cookie is still unset. The email
   code after that is the key gate, not a second factor. Clients that omit
   has_keys are not this page.

   Forgot password emails a code. It does not reveal whether the username
   exists. A new password does not open the old sealed backup.
*/
import {exportPublic, openRecovery, sealRecovery} from "./crypto.js?v=2";

/* 0.19: a link shared from another app (Android share sheet -> /share -> /?share=)
   rides through every sign-in step (password, authenticator, email code).
   sessionStorage keeps it if the page reloads. It is never sent automatically. */
const SHARE_KEY = "bc-share";

/* 26.10.9.1 hotfix: emailed codes can be pasted as "123 456", "123-456" or with a stray space.
   Keep only the digits (max 6) as the person types, pastes or autofills. */
function onlyDigits(value) { return String(value || "").replace(/\D+/g, "").slice(0, 6); }
for (const id of ["code", "reset-code"]) {
  const box = document.getElementById(id);
  if (!box) continue;
  box.addEventListener("input", () => { const v = onlyDigits(box.value); if (v !== box.value) box.value = v; });
}
// Text shared to Branch Chat from another app (?share=), kept until after sign-in.
function readShare() {
  let value = "";
  try { value = new URLSearchParams(location.search).get("share") || ""; } catch (err) { value = ""; }
  value = value.slice(0, 2000);
  if (value) {
    try { sessionStorage.setItem(SHARE_KEY, value); } catch (err) { /* the query string still carries it */ }
    return value;
  }
  try { return sessionStorage.getItem(SHARE_KEY) || ""; } catch (err) { return ""; }
}
const pendingShare = readShare();

/* 0.23.1: a group invite link (/join#j1.<secret>) opened while signed out. The secret after "#" never reaches the
   server; keep it in this browser for a day so the app can offer "Join group?" right after sign-in (or after making
   an account with an app invite), and take it out of the address bar. */
(function keepGroupInvite() {
  const hash = location.hash || "";
  if (!/^#j1\.[A-Za-z0-9_-]{43}$/.test(hash)) return;
  try { localStorage.setItem("bc-pending-join", JSON.stringify({h: hash, t: Date.now()})); } catch (err) { return; }
  history.replaceState(null, "", "/");
  const note = document.getElementById("login-note");
  if (note) note.textContent = "Sign in to join the group you were invited to. New to BranchChat? Ask the person who sent the link for an app invite first, then open the group link again.";
})();

/* 0.23.3: "Sign in" from the branchchat.app invite landing page arrives as /#u=<username>: fill it in, focus the
   password, and take it out of the address bar. The password is only ever typed here, on this server. */
(function prefillUser() {
  const m = /^#u=([A-Za-z0-9._-]{3,32})$/.exec(location.hash || "");
  if (!m) return;
  history.replaceState(null, "", location.pathname + location.search);
  const box = document.getElementById("username");
  if (box) { box.value = decodeURIComponent(m[1]); setTimeout(() => document.getElementById("password").focus(), 0); }
})();

const secureOn = window.isSecureContext;
document.getElementById("secure-led").classList.toggle("on", secureOn);
document.getElementById("secure-text").textContent = secureOn ? "Secure connection" : "Not a secure connection";

const emailedLead = "A BranchChat code was emailed to this account. Enter it to finish signing in. It expires in 10 minutes. The code also unlocks a saved key backup.";
const failedLead = "The code was not emailed. You can ask for another code. Signing in stays closed until a code is accepted.";

// Open this browser's key store (IndexedDB) where the device keys live.
function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("huzz-chat", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// Read one value from the key store.
async function idbGet(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const req = db.transaction("kv").objectStore("kv").get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

// Write one value to the key store.
async function idbSet(key, value) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const req = db.transaction("kv", "readwrite").objectStore("kv").put(value, key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

// Save a password-protected backup of this device's keys on the server (only when it has none yet).
async function uploadBackup(password, stored) {
  // Only a browser that already has keys reaches this, and only when the server has no backup yet.
  const packed = JSON.parse(await sealRecovery(stored.deviceId, stored.privateKey, stored.publicKey, password));
  const res = await fetch("/api/account/key-backup", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({salt: packed.salt, iv: packed.iv, ct: packed.ct}),
  });
  if (!res.ok) throw new Error("backup");
}

let unlockPassword = "";
let loginTicket = "";
let factorTicket = "";
let factorPassword = "";
let factorStored = null;
let factorHasKeys = false;

// Show one sign-in panel (login, authenticator, emailed code or password reset) and hide the rest.
function showPanel(name) {
  document.getElementById("login").hidden = name !== "login";
  document.getElementById("factor").hidden = name !== "factor";
  document.getElementById("unlock").hidden = name !== "unlock";
  document.getElementById("reset").hidden = name !== "reset";
}

// Show the "enter the emailed code" panel.
function showUnlock(password, data) {
  unlockPassword = password;
  loginTicket = data.ticket || "";
  document.getElementById("code").value = "";
  document.getElementById("unlock-lead").textContent = data.emailed ? emailedLead : failedLead;
  document.getElementById("unlock-error").textContent = data.emailed ? "" : (data.error || "The code was not emailed.");
  showPanel("unlock");
  document.getElementById("code").focus();
}

// Show the authenticator code panel.
function showFactor(password, stored, hasKeys, ticket) {
  factorPassword = password;
  factorStored = stored;
  factorHasKeys = hasKeys;
  factorTicket = ticket || "";
  document.getElementById("factor-code").value = "";
  document.getElementById("factor-error").textContent = "";
  showPanel("factor");
  document.getElementById("factor-code").focus();
}

// After the password: ask for a code if needed, restore or back up keys, then open the app.
async function finishSignedIn(password, stored, hasKeys, data) {
  if (data.need_code) {
    if (!data.ticket) {
      document.getElementById("error").textContent = "Sign in again.";
      showPanel("login");
      return;
    }
    showUnlock(password, data);
    return;
  }
  if (hasKeys && stored && !data.key_backup) {
    try { await uploadBackup(password, stored); } catch (err) { /* the app can save it later */ }
  }
  factorPassword = "";
  unlockPassword = "";
  enterApp("/");
}

document.getElementById("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("error");
  error.textContent = "";
  document.getElementById("login-note").textContent = "";
  const password = document.getElementById("password").value;
  document.getElementById("password").value = "";
  let stored = null;
  try { stored = await idbGet("device"); } catch (err) { stored = null; }
  const hasKeys = !!(stored && stored.deviceId && stored.privateKey);
  let res;
  try {
    res = await fetch("/api/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        username: document.getElementById("username").value,
        password,
        has_keys: hasKeys,
      }),
    });
  } catch (err) {
    error.textContent = "The page could not reach BranchChat.";
    return;
  }
  let data = {};
  try { data = await res.json(); } catch (err) { data = {}; }
  if (!res.ok) {
    error.textContent = data.error || "That login was not accepted.";
    return;
  }
  if (data.need_totp) {
    if (!data.ticket) {
      error.textContent = "Sign in again.";
      return;
    }
    showFactor(password, stored, hasKeys, data.ticket);
    return;
  }
  await finishSignedIn(password, stored, hasKeys, data);
});

document.getElementById("factor").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("factor-error");
  error.textContent = "";
  const code = document.getElementById("factor-code").value.trim();
  let res;
  let data = {};
  try {
    res = await fetch("/api/login/totp", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ticket: factorTicket, code, has_keys: factorHasKeys}),
    });
    data = await res.json().catch(() => ({}));
  } catch (err) {
    error.textContent = "The page could not reach BranchChat.";
    return;
  }
  if (!res.ok) {
    error.textContent = data.error || "That code was not accepted.";
    return;
  }
  const password = factorPassword;
  const stored = factorStored;
  const hasKeys = factorHasKeys;
  document.getElementById("factor-code").value = "";
  await finishSignedIn(password, stored, hasKeys, data);
});

document.getElementById("factor-back").addEventListener("click", () => {
  factorPassword = "";
  factorStored = null;
  factorTicket = "";
  document.getElementById("factor-code").value = "";
  showPanel("login");
});

document.getElementById("send-code").addEventListener("click", async () => {
  const error = document.getElementById("unlock-error");
  error.textContent = "";
  try {
    const res = await fetch("/api/login/resend", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ticket: loginTicket}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      error.textContent = data.error || "The code was not emailed.";
      return;
    }
    document.getElementById("unlock-lead").textContent = emailedLead;
  } catch (err) {
    error.textContent = "The page could not reach BranchChat.";
  }
});

document.getElementById("unlock").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("unlock-error");
  error.textContent = "";
  const code = onlyDigits(document.getElementById("code").value);
  let res;
  let data = {};
  try {
    res = await fetch("/api/login/code", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ticket: loginTicket, code}),
    });
    data = await res.json().catch(() => ({}));
  } catch (err) {
    error.textContent = "The page could not reach BranchChat.";
    return;
  }
  if (!res.ok) {
    error.textContent = data.error || "That code was not accepted.";
    return;
  }
  if (data.salt && data.iv && data.ct) {
    let opened;
    try {
      opened = await openRecovery(JSON.stringify({v: 1, salt: data.salt, iv: data.iv, ct: data.ct}), unlockPassword);
    } catch (err) {
      error.textContent = "That password did not open the backup. Ask for a new code and try again.";
      return;
    }
    if (!opened || !opened.deviceId || String(opened.deviceId).length !== 32) {
      error.textContent = "That key backup was not recognized.";
      return;
    }
    try {
      await idbSet("device", opened);
    } catch (err) {
      error.textContent = "This device could not save the message keys.";
      return;
    }
    let done;
    let doneData = {};
    try {
      done = await fetch("/api/login/finish", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ticket: data.finish || ""}),
      });
      doneData = await done.json().catch(() => ({}));
    } catch (err) {
      error.textContent = "The page could not reach BranchChat.";
      return;
    }
    if (!done.ok) {
      showPanel("login");
      document.getElementById("error").textContent = doneData.error || "Sign in again.";
      return;
    }
    try {
      await fetch("/api/devices", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({device_id: opened.deviceId, public_key: await exportPublic(opened.publicKey)}),
      });
    } catch (err) { /* the app registers this device again */ }
  }
  unlockPassword = "";
  factorPassword = "";
  loginTicket = "";
  enterApp("/");
});

document.getElementById("forgot").addEventListener("click", () => {
  document.getElementById("reset-user").value = document.getElementById("username").value;
  document.getElementById("reset-error").textContent = "";
  document.getElementById("reset-note").textContent = "";
  document.getElementById("reset-next").hidden = true;
  document.getElementById("reset-send").textContent = "Email a reset code";
  showPanel("reset");
  document.getElementById("reset-user").focus();
});

// 26.10.13.8: a reset link from an admin email opens straight on the reset form (#reset=<username>&token=<token>).
// The token stays in the fragment (never sent in a request line) and is cleared from the address bar at once.
let resetToken = "";
(function () {
  let h = null;
  try { h = new URLSearchParams((location.hash || "").slice(1)); } catch (err) { h = null; }
  if (!h || !h.get("reset") || !/^[A-Za-z0-9_-]{40,64}$/.test(h.get("token") || "")) return;
  resetToken = h.get("token");
  try { history.replaceState(null, "", location.pathname + location.search); } catch (err) { /* ignore */ }
  document.getElementById("reset-user").value = h.get("reset");
  document.getElementById("reset-next").hidden = false;
  const codeBox = document.getElementById("reset-code"); codeBox.hidden = true;
  const codeLabel = document.querySelector('label[for="reset-code"]'); if (codeLabel) codeLabel.hidden = true;
  document.getElementById("reset-send").textContent = "Set new password";
  document.getElementById("reset-note").textContent = "Choose a new password for " + h.get("reset") + ".";
  showPanel("reset");
  setTimeout(() => document.getElementById("reset-pass").focus(), 0);
})();

document.getElementById("reset-back").addEventListener("click", () => {
  document.getElementById("reset-pass").value = "";
  document.getElementById("reset-again").value = "";
  document.getElementById("reset-factor").value = "";
  showPanel("login");
});

// 0.21.07: the reset form is novalidate; say what is wrong, mark that field and put the cursor there.
function resetBad(id, text) {
  for (const f of ["reset-user", "reset-code", "reset-pass", "reset-again"]) document.getElementById(f).removeAttribute("aria-invalid");
  document.getElementById("reset-error").textContent = text;
  document.getElementById(id).setAttribute("aria-invalid", "true");
  document.getElementById(id).focus();
}
for (const f of ["reset-user", "reset-code", "reset-pass", "reset-again"]) document.getElementById(f).addEventListener("input", () => document.getElementById(f).removeAttribute("aria-invalid"));
document.getElementById("reset").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("reset-error");
  const note = document.getElementById("reset-note");
  error.textContent = "";
  note.textContent = "";
  const username = document.getElementById("reset-user").value.trim();
  if (!username) { resetBad("reset-user", "Enter your username."); return; }
  if (!document.getElementById("reset-next").hidden) {
    const password = document.getElementById("reset-pass").value;
    const again = document.getElementById("reset-again").value;
    if (!resetToken && !/^\d{6}$/.test(onlyDigits(document.getElementById("reset-code").value))) { resetBad("reset-code", "Enter the 6-digit code from the email."); return; }
    if (password.length < 8) { resetBad("reset-pass", "Password must be at least 8 characters."); return; }
    if (password.length > 200) { resetBad("reset-pass", "Password can be at most 200 characters."); return; }
    if (password !== again) { resetBad("reset-again", "Passwords don't match."); return; }
    let res;
    let data = {};
    try {
      res = await fetch("/api/password/reset/finish", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          username,
          code: resetToken || onlyDigits(document.getElementById("reset-code").value),
          password,
          factor: document.getElementById("reset-factor").value.trim(),
        }),
      });
      data = await res.json().catch(() => ({}));
    } catch (err) {
      error.textContent = "The page could not reach BranchChat.";
      return;
    }
    document.getElementById("reset-pass").value = "";
    document.getElementById("reset-again").value = "";
    if (data.need_totp) {
      error.textContent = "Enter the code from your authenticator app, or a backup code.";
      document.getElementById("reset-factor").focus();
      return;
    }
    if (!res.ok) {
      error.textContent = resetToken ? "This reset link has expired or was already used. Ask for a new one." : (data.error || "That code was not accepted.");
      return;
    }
    document.getElementById("reset-code").value = "";
    document.getElementById("reset-factor").value = "";
    resetToken = "";
    showPanel("login");
    document.getElementById("error").textContent = "";
    document.getElementById("username").value = username;
    document.getElementById("login-note").textContent = data.note || "Password updated. Sign in with the new password.";
    return;
  }
  let res;
  let data = {};
  try {
    res = await fetch("/api/password/reset/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username}),
    });
    data = await res.json().catch(() => ({}));
  } catch (err) {
    error.textContent = "The page could not reach BranchChat.";
    return;
  }
  if (!res.ok) {
    error.textContent = data.error || "That reset could not be started.";
    return;
  }
  document.getElementById("reset-next").hidden = false;
  document.getElementById("reset-send").textContent = "Set new password";
  note.textContent = data.note || "If that account can receive email, a reset code is on its way. It expires in 10 minutes.";
  document.getElementById("reset-code").focus();
});

/* Login fade: native view transition where supported, class fallback elsewhere. */
function enterApp(url = "/") {
  const shared = readShare() || pendingShare;
  if (shared && url === "/") url = "/?share=" + encodeURIComponent(shared);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const vt = "onpagereveal" in window;
  if (reduce || vt) { location.href = url; return; }
  try { sessionStorage.setItem("bc-just-signed-in", "1"); } catch (err) {}
  const card = [...document.querySelectorAll("form")].find((f) => !f.hidden) || document.body;
  card.classList.add("bc-leaving");
  setTimeout(() => { location.href = url; }, 430);
}

/* 0.23.0: Chromium can skip the fade into the app and reject the transition's promise as an uncaught error.
   A skipped fade is harmless, so mark it handled on both sides of the page change. */
for (const type of ["pageswap", "pagereveal"]) {
  addEventListener(type, (e) => {
    const vt = e && e.viewTransition;
    if (!vt) return;
    for (const k of ["ready", "finished", "updateCallbackDone"]) if (vt[k] && vt[k].catch) vt[k].catch(() => {});
  });
}
