/* Front door, before first paint: the apps already identify themselves in the User-Agent (the server renders the
   app version from that). If a cache or proxy ever strips that, the Android bridge or the UA seen here still
   switches the page to the app version: no download links inside the apps. */
(function () {
  try {
    var r = document.documentElement, ua = navigator.userAgent || "";
    if (r.getAttribute("data-bc-app")) return;
    if (/\bBranchChatDesktop\//.test(ua)) r.setAttribute("data-bc-app", "desktop");
    else if (window.BranchChatApp || /\bBranchChatApp\//.test(ua)) r.setAttribute("data-bc-app", "android");
  } catch (err) { /* layout only */ }
})();

/* 26.10.6: register the service worker on the front door too, so a first-time, signed-out visitor gets it
   (offline page + Chromium "Install app"). Same URL, scope and options as app.js syncPush(), so the browser keeps
   one registration (register() with the same script URL returns the existing one). Not inside the apps. */
(function () {
  try {
    var r = document.documentElement;
    if (r.getAttribute("data-bc-app") || !("serviceWorker" in navigator) || !window.isSecureContext) return;
    var go = function () {
      navigator.serviceWorker.getRegistration("/").then(function (reg) {
        if (reg && reg.active && /\/sw\.js$/.test(reg.active.scriptURL)) { reg.update().catch(function () {}); return; }
        return navigator.serviceWorker.register("/sw.js", {scope: "/", type: "module", updateViaCache: "none"});
      }).catch(function (err) { try { console.warn("BranchChat: service worker not registered", err); } catch (e) {} });
    };
    if (document.readyState === "complete") go(); else window.addEventListener("load", go);
  } catch (err) { /* optional */ }
})();

/* 26.10.6: Chromium/Android "Install app". Keep the browser's beforeinstallprompt so fd-install.js can show
   our own button (it may fire before that deferred script runs). */
(function () {
  try {
    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();
      window.__bcInstallPrompt = e;
      document.dispatchEvent(new CustomEvent("bc-installable"));
    });
    window.addEventListener("appinstalled", function () {
      window.__bcInstallPrompt = null;
      document.dispatchEvent(new CustomEvent("bc-installed"));
    });
  } catch (err) { /* optional */ }
})();
