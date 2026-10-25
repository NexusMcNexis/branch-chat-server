/* Branch Chat public instance: Sign up / Log in first screen (v0.22.8-public.2).
   Only served when the server runs with PUBLIC_MODE=1 or BC_OPEN_SIGNUP=1. login.js (sign-in,
   authenticator, email code, password reset) is unchanged; this file adds the Sign up tab.
   Rules match the server: usernames 3-32 letters, numbers, dots, dashes, underscores; passwords 8-200. */
const $ = (id) => document.getElementById(id);
const USER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const RETURNING = "bc-returning";
const signup = $("signup"), login = $("login"), tabs = $("auth-tabs");
const others = ["factor", "reset", "unlock"].map($).filter(Boolean);

const secureOn = window.isSecureContext;
for (const led of document.querySelectorAll(".signup-led")) led.classList.toggle("on", secureOn);
for (const t of document.querySelectorAll(".signup-secure")) t.textContent = secureOn ? "Secure connection" : "Not a secure connection";

// Switch between the Log in and Sign up forms.
function show(which) {
  const isSignup = which === "signup";
  signup.hidden = !isSignup;
  login.hidden = isSignup;
  for (const f of others) f.hidden = true;
  $("tab-signup").setAttribute("aria-selected", String(isSignup));
  $("tab-login").setAttribute("aria-selected", String(!isSignup));
  tabs.hidden = false;
  const first = isSignup ? $("su-username") : $("username");
  if (first && !matchMedia("(pointer: coarse)").matches) setTimeout(() => first.focus(), 0);
}
// The tabs belong to the first screen only: hide them while login.js shows its later steps.
function syncTabs() {
  const onFirst = !signup.hidden || !login.hidden;
  // Only write on change: re-setting the attribute queues another mutation and loops forever.
  if (tabs.hidden !== !onFirst) tabs.hidden = !onFirst;
}
new MutationObserver(syncTabs).observe(document.body, {subtree: true, attributes: true, attributeFilter: ["hidden"]});

$("tab-signup").addEventListener("click", () => show("signup"));
$("tab-login").addEventListener("click", () => show("login"));
$("su-to-login").addEventListener("click", () => show("login"));
tabs.addEventListener("keydown", (ev) => {
  if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
  const next = $("tab-signup").getAttribute("aria-selected") === "true" ? "login" : "signup";
  show(next);
  $(next === "signup" ? "tab-signup" : "tab-login").focus();
});
let returning = false;
try { returning = localStorage.getItem(RETURNING) === "1"; } catch (err) {}
show(returning ? "login" : "signup");

/* ---------- Turnstile (only when the server set a site key) ---------- */
const siteKey = (document.querySelector('meta[name="bc-turnstile"]') || {}).content || "";
let tsToken = "", tsWidget = null;
if (siteKey) {
  const box = $("su-turnstile");
  box.hidden = false;
  const start = () => {
    if (!window.turnstile) return false;
    tsWidget = window.turnstile.render(box, {
      sitekey: siteKey,
      theme: document.documentElement.dataset.scheme === "light" ? "light" : "dark",
      callback: (t) => { tsToken = t; },
      "expired-callback": () => { tsToken = ""; },
      "error-callback": () => { tsToken = ""; },
    });
    return true;
  };
  if (!start()) { const timer = setInterval(() => { if (start()) clearInterval(timer); }, 300); setTimeout(() => clearInterval(timer), 20000); }
}

/* ---------- sign up ---------- */
const fields = ["su-username", "su-email", "su-password", "su-again", "su-agree"];
// Remove the red "invalid" marks from every sign-up field.
function clearInvalid() { for (const id of fields) $(id).removeAttribute("aria-invalid"); }
// Show a sign-up error, mark the field and focus it. Returns false.
function fail(id, text) {
  clearInvalid();
  $("su-error").textContent = text;
  if (id && $(id)) { $(id).setAttribute("aria-invalid", "true"); $(id).focus(); }
  return false;
}
for (const id of fields) $(id).addEventListener("input", () => { if ($(id).getAttribute("aria-invalid")) { $(id).removeAttribute("aria-invalid"); $("su-error").textContent = ""; } });
// Check the sign-up fields (username, email, password) before sending.
function check() {
  const u = $("su-username").value.trim(), e = $("su-email").value.trim(), p = $("su-password").value;
  if (u.length < 3 || u.length > 32) return fail("su-username", "Username must be 3\u201332 characters.");
  if (!USER_RE.test(u)) return fail("su-username", "Usernames can use letters, numbers, dots, dashes and underscores, and must start with a letter or number.");
  if (!EMAIL_RE.test(e) || e.length > 200) return fail("su-email", "Enter a real email address.");
  if (p.length < 8) return fail("su-password", "Password must be at least 8 characters.");
  if (p.length > 200) return fail("su-password", "Password can be at most 200 characters.");
  if (p !== $("su-again").value) return fail("su-again", "Passwords don't match.");
  if (!$("su-agree").checked) return fail("su-agree", "Tick the box to agree before creating an account.");
  if (siteKey && !tsToken) return fail("", "Finish the human check first.");
  clearInvalid();
  return true;
}
let busy = false;
signup.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (busy) return;
  $("su-error").textContent = "";
  if (!check()) return;
  busy = true;
  const button = $("su-go");
  button.disabled = true;
  $("su-error").textContent = "";
  const prior = button.textContent;
  button.textContent = "Creating your account\u2026";
  let res, data = {};
  try {
    res = await fetch("/api/signup", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: $("su-username").value.trim(), email: $("su-email").value.trim(), password: $("su-password").value, agree: true, turnstile: tsToken}),
    });
    try { data = await res.json(); } catch (err) { data = {}; }
  } catch (err) {
    res = null;
  }
  if (!res || !res.ok) {
    busy = false; button.disabled = false; button.textContent = prior;
    const map = {username: "su-username", email: "su-email", password: "su-password", agree: "su-agree"};
    fail(map[data.field] || "", res ? (data.error || "That sign-up was not accepted.") : "The page could not reach BranchChat. Check the connection and try again.");
    if (tsWidget !== null && window.turnstile) { try { window.turnstile.reset(tsWidget); } catch (err) {} tsToken = ""; }
    return;
  }
  try { localStorage.setItem(RETURNING, "1"); } catch (err) {}
  $("su-password").value = ""; $("su-again").value = "";
  location.replace("/" + location.search);
});
login.addEventListener("submit", () => { try { localStorage.setItem(RETURNING, "1"); } catch (err) {} });
