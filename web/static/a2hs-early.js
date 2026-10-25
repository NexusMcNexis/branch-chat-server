/* 0.22.7 Add to Home Screen: keep the browser's install prompt (Chromium only) so the menu button can open it later.
   Loaded in <head> because the event can fire before the app module runs. */
(function () {
  window.addEventListener("beforeinstallprompt", function (ev) {
    ev.preventDefault();
    window.__bcInstall = ev;
    document.dispatchEvent(new Event("bc-install-ready"));
  });
  window.addEventListener("appinstalled", function () {
    window.__bcInstall = null;
    window.__bcInstalled = true;
    document.dispatchEvent(new Event("bc-installed"));
  });
})();
