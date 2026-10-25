/* Front door: Apple install directions (Oct 19 2026). On iPhone, iPad and Mac the "Get the app" link (/get, downloads
   that don't apply there) is swapped for short "install as a web app" steps. Every other OS keeps the page exactly as
   it was. Self-contained: does nothing unless the front door's .fd-getapp link is on the page, never inside the apps,
   never when already running as an installed web app. Wording follows docs/BranchChat-Install-Guide.md. */
(function () {
  "use strict";
  function detect(nav, win) {
    var ua = nav.userAgent || "", plat = nav.platform || "", touch = nav.maxTouchPoints || 0;
    if (/iPhone|iPod/.test(ua)) return "iphone";
    if (/iPad/.test(ua)) return "ipad";
    var macish = /Macintosh|Mac OS X/.test(ua) || /^Mac/.test(plat);
    if (macish && (touch > 1 || "ontouchend" in win.document)) return "ipad";   // iPadOS 13+ Safari says "Macintosh"
    if (macish) return "mac";
    return "";
  }
  function isSafari(ua) {   // real Safari on a Mac: no Chrome/Chromium/Edge/Firefox/Opera/Brave tokens
    return /Safari\//.test(ua) && /Version\//.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|Firefox|Edg|OPR|Brave/.test(ua);
  }
  function standalone(win) {
    try { if (win.navigator.standalone === true) return true; } catch (e) {}
    try { return !!(win.matchMedia && win.matchMedia("(display-mode: standalone)").matches); } catch (e) { return false; }
  }
  var STEPS = {
    iphone: { title: "Install BranchChat on your iPhone", steps: [
      "Open this page in <b>Safari</b>.",
      "Tap the <b>Share</b> button (the square with an arrow pointing up, in the bottom bar).",
      "Tap <b>Add to Home Screen</b>. (Scroll down if you don't see it.)",
      "Leave the <b>\u201cOpen as web app\u201d</b> toggle turned on, then tap <b>Add</b>."],
      after: "Open BranchChat from the home-screen icon: it runs full screen, and notifications work once you allow them." },
    ipad: { title: "Install BranchChat on your iPad", steps: [
      "Open this page in <b>Safari</b>.",
      "Tap the <b>Share</b> button (the square with an arrow pointing up, in the top bar).",
      "Tap <b>Add to Home Screen</b>. (Scroll down if you don't see it.)",
      "Leave the <b>\u201cOpen as web app\u201d</b> toggle turned on, then tap <b>Add</b>."],
      after: "Open BranchChat from the home-screen icon: it runs full screen, and notifications work once you allow them." },
    mac: { title: "Install BranchChat on your Mac", steps: [
      "In the menu bar, click <b>File \u2192 Add to Dock</b> (or the <b>Share</b> button \u2192 <b>Add to Dock</b>).",
      "Keep the name as BranchChat and click <b>Add</b>.",
      "Open BranchChat from the <b>Dock or Launchpad</b>: it runs in its own window, with no browser bars.",
      "<b>Allow notifications</b> when asked."],
      after: "Needs macOS Sonoma (14) or newer with Safari 17+." },
    macother: { title: "Install BranchChat on your Mac", steps: [],
      after: "Open <b>branchchat.app</b> in <b>Safari</b> (macOS Sonoma or newer), then choose <b>File \u2192 Add to Dock</b> to install it as an app." }
  };
  function build(kind) {
    var s = STEPS[kind], d = document.createElement("details");
    d.className = "fd-install"; d.id = "fd-install"; d.setAttribute("data-os", kind);
    var html = '<summary>' + s.title + '</summary>';
    if (s.steps.length) html += '<ol>' + s.steps.map(function (t) { return "<li>" + t + "</li>"; }).join("") + "</ol>";
    html += '<p class="fd-install-after">' + s.after + "</p>";
    // Small donate link at the bottom of the install steps.
    //
    // (Alternative label if preferred: "Buy me a coffee".)
    html += '<p class="fd-install-donate"><a class="bc-kofi" href="https://ko-fi.com/branchchat" target="_blank" rel="noopener noreferrer">Support BranchChat \u2615</a></p>';
    d.innerHTML = html;   // static strings above only, no user data
    return d;
  }
  function run() {
    var r = document.documentElement, link = document.querySelector(".fd-getapp, .fd-donate");   /* browsers-only footer marker */
    if (!link || r.getAttribute("data-bc-app") || standalone(window)) return;
    var kind = detect(navigator, window);
    if (!kind) return;                                   // Windows, Android, Linux, ...: unchanged
    if (kind === "mac" && !isSafari(navigator.userAgent || "")) kind = "macother";
    var side = document.getElementById("fd-side");      // two-column layout: the card heads the right column
    var anchor = document.getElementById("fd-explore") || document.getElementById("fd-server");
    if (side) side.insertBefore(build(kind), side.firstChild);
    else if (anchor) anchor.parentNode.insertBefore(build(kind), anchor.nextSibling);
    else return;
    if (link.classList.contains("fd-getapp")) { link.hidden = true; link.style.display = "none"; }
    r.setAttribute("data-fd-os", kind);
  }
  /* 26.10.6: Android / Chromium (and desktop Chrome/Edge): when the browser says BranchChat can be installed
     (beforeinstallprompt, stashed by fd-early.js), show an "Install app" button where Apple shows its steps.
     Apple keeps the steps above; Firefox/Safari never fire the event, so nothing changes there. */
  function placeCard(el) {
    var side = document.getElementById("fd-side");
    var anchor = document.getElementById("fd-explore") || document.getElementById("fd-server");
    if (side) side.insertBefore(el, side.firstChild);
    else if (anchor) anchor.parentNode.insertBefore(el, anchor.nextSibling);
    else return false;
    return true;
  }
  function installButton() {
    var r = document.documentElement;
    if (!window.__bcInstallPrompt || r.getAttribute("data-bc-app") || standalone(window)) return;
    if (document.getElementById("fd-pwa") || document.getElementById("fd-install")) return;   // Apple steps win
    if (!document.querySelector(".fd-getapp, .fd-donate")) return;
    var box = document.createElement("div");
    box.className = "fd-pwa"; box.id = "fd-pwa";
    box.innerHTML = '<button type="button" class="fd-pwa-btn" id="fd-pwa-btn">Install app</button>' +
      '<p class="fd-install-after">Adds BranchChat to your home screen or apps, with its own window and notifications.</p>';
    if (!placeCard(box)) return;
    r.setAttribute("data-fd-pwa", "1");
    document.getElementById("fd-pwa-btn").addEventListener("click", function () {
      var ev = window.__bcInstallPrompt;
      if (!ev) return;
      window.__bcInstallPrompt = null;
      ev.prompt();
      Promise.resolve(ev.userChoice).then(function (choice) {
        try { console.log("BranchChat install: " + (choice && choice.outcome)); } catch (e) {}
        if (choice && choice.outcome === "accepted") box.remove();
      }).catch(function () {});
    });
  }
  document.addEventListener("bc-installable", function () { setTimeout(installButton, 0); });
  document.addEventListener("bc-installed", function () { var b = document.getElementById("fd-pwa"); if (b) b.remove(); });
  window.BCInstallButton = installButton;   // for tests
  window.BCInstallDetect = detect;   // for tests
  function both() { run(); installButton(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", both); else both();
})();
