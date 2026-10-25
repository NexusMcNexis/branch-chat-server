try {
  var mode = localStorage.getItem("huzz-chat-mode") || localStorage.getItem("huzz-chat-theme") || "system";
  if (mode !== "light" && mode !== "dark") mode = "system";
  /* 26.10.13.2: palettes renamed (names only, colors unchanged). Saved choices under the old keys, both the
     everyone setting and the per-chat map, are moved to the new keys before the first paint. */
  var renamed = {telegram:"glacier", discord:"nebula", whatsapp:"lagoon", messages:"bubble", slack:"aubergine", spotify:"vinyl", github:"graphite"};
  var savedPal = localStorage.getItem("huzz-chat-palette");
  if (savedPal && renamed[savedPal]) localStorage.setItem("huzz-chat-palette", renamed[savedPal]);
  try {
    var byChat = JSON.parse(localStorage.getItem("huzz-chat-palette-by-chat") || "{}"), moved = false;
    for (var ck in byChat) if (renamed[byChat[ck]]) { byChat[ck] = renamed[byChat[ck]]; moved = true; }
    if (moved) localStorage.setItem("huzz-chat-palette-by-chat", JSON.stringify(byChat));
  } catch (e) {}
  var known = {huzz:1, glacier:1, nebula:1, lagoon:1, bubble:1, aubergine:1, vinyl:1, graphite:1, forest:1, ocean:1, sunset:1, lavender:1, ember:1, midnight:1, rose:1, mint:1};
  var palette = localStorage.getItem("huzz-chat-palette") || "huzz";
  if (!known[palette]) palette = "huzz";
  var dark = mode === "dark" || (mode !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  var root = document.documentElement;
  root.dataset.mode = mode; root.dataset.palette = palette; root.dataset.scheme = dark ? "dark" : "light";
} catch (err) {}
/* 26.10.8 Lite mode, set before the first paint: html.lite turns off the live blur behind panels and bubbles and draws
   moving backgrounds at 30 fps. localStorage bc-lite: "1" on, "0" off, unset = automatic (on for slower devices:
   Data Saver, 4 GB of memory or less, or a touch screen with 4 cores or fewer). app.js has the switch. */
try {
  (function () {
    var n = navigator, pref = localStorage.getItem("bc-lite");
    var coarse = window.matchMedia && matchMedia("(pointer: coarse)").matches;
    var auto = !!((n.connection && n.connection.saveData) || (n.deviceMemory && n.deviceMemory <= 4) || (coarse && n.hardwareConcurrency && n.hardwareConcurrency <= 4));
    window.bcLiteAuto = auto;
    document.documentElement.classList.toggle("lite", pref === "1" || (pref !== "0" && auto));
  })();
} catch (err) {}
// 0.23.0: Chromium sometimes skips the page fade on arrival and rejects the transition's promise, which shows up
// as an uncaught error. A skipped fade is harmless, so mark it handled.
try {
  // 0.23.1: also the leaving side (pageswap), which Chromium can abort when a link is opened from the app.
  ["pagereveal", "pageswap"].forEach(function (type) {
    addEventListener(type, function (e) {
      var vt = e && e.viewTransition;
      if (!vt) return;
      ["ready", "finished", "updateCallbackDone"].forEach(function (k) { if (vt[k] && vt[k].catch) vt[k].catch(function () {}); });
    });
  });
  // Some Chromium builds still report an aborted page fade before those handlers run. Only that exact case is hushed.
  addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason;
    if (r && (r.name === "InvalidStateError" || r.name === "AbortError") && /transition/i.test(String(r.message || ""))) e.preventDefault();
  });
} catch (err) {}
