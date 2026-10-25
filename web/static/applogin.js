/* 26.10.10 browser -> Android app sign-in hand-off (see app-login.html). The token is only ever after "#". */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const q = new URLSearchParams(location.search);
  const frag = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  const token = /^[A-Za-z0-9_-]{20,100}$/.test(frag.get("h") || "") ? frag.get("h") : "";
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);   // drop the token from the address
  const inApp = !!window.BranchChatApp;
  const s = q.get("s") || "";
  const fail = (text) => { $("lead").textContent = text; };

  if (s) {   // relay on the home server
    const ok = /^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$/i.test(s);
    const origin = ok ? "https://" + s.toLowerCase() : "";
    if (!origin) { fail("This link is not valid."); return; }
    if (!inApp) { location.replace(origin + "/"); return; }   // no app: back to the server in this browser
    if (!token) { location.replace(origin + "/"); return; }
    $("lead").textContent = "Sign in to this server in the BranchChat app?";
    $("host").textContent = s.toLowerCase();
    $("host").hidden = false;
    $("go").hidden = false;
    $("go").textContent = "Continue to " + s.toLowerCase();
    $("applogin").addEventListener("submit", (ev) => {
      ev.preventDefault();
      $("go").disabled = true;
      location.assign(origin + "/app-login#h=" + encodeURIComponent(token));
    });
    return;
  }
  if (!inApp || !token) { location.replace("/"); return; }
  (async () => {
    let res, data = {};
    try {
      res = await fetch("/api/handoff/redeem", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
      try { data = await res.json(); } catch (err) { data = {}; }
    } catch (err) { fail("Couldn't reach BranchChat. Check the connection and sign in."); setTimeout(() => location.replace("/"), 2500); return; }
    if (!res.ok) { fail(data.error || "That sign-in link expired."); setTimeout(() => location.replace("/"), 2500); return; }
    location.replace("/");   // the app makes this device's own message key on first load (multi-device flow)
  })();
})();
