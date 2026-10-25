/* 26.10.12 (Luke): front door. Sign-in stays folded behind "Sign in on web" until tapped, and "Create account" first
   asks which server you're joining. This server -> its own sign-up form; another server -> that server's page
   (invite-only servers show their own message). Runs after frontdoor.js and only re-uses its buttons. */
const $ = (id) => document.getElementById(id);
const gate = $("fd-gate"), pick = $("fd-pick"), signup = $("signup"), login = $("login");
if (gate && pick && signup && login) {
  const explicit = /^#(u=|own|lobby)/.test(location.hash || "") || new URLSearchParams(location.search).has("login");
  const fold = () => { signup.hidden = true; login.hidden = true; pick.hidden = true; };
  if (!explicit) fold();
  $("gate-signin").addEventListener("click", () => {
    const open = login.hidden;
    fold();
    if (open) $("su-to-login").click();
    $("gate-signin").setAttribute("aria-expanded", String(open));
  });
  $("gate-create").addEventListener("click", () => { fold(); pick.hidden = false; $("gate-signin").setAttribute("aria-expanded", "false"); });
  pick.addEventListener("change", () => { $("pick-other-box").hidden = !$("pick-other").checked; });
  $("pick-back").addEventListener("click", fold);
  pick.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const v = (pick.querySelector('input[name="srv"]:checked') || {}).value || "none";
    let host = v === "none" || v === "public" ? "branchchat.app" : v === "woods" ? "chat.lastnpc.com" : "";
    if (v === "other") {
      try { host = new URL(/^https?:\/\//i.test($("pick-url").value.trim()) ? $("pick-url").value.trim() : "https://" + $("pick-url").value.trim()).host; } catch (e) { host = ""; }
      if (!host || !host.includes(".")) { $("pick-error").textContent = "Enter the server address, like chat.example.org."; return; }
    }
    if (host === location.host) { pick.hidden = true; $("to-signup").click(); return; }
    location.href = "https://" + host + "/";
  });
}
