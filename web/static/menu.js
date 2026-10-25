/* Branch Chat menu reorganization (v0.23.3-beta.6, order B; beta.8: "Delete animations" heading + picker inside Animation). Ported from the approved mockup (mockups/menu-reorg proto.js).
   Runs after app.js (deferred, listed after it). app.js still owns every setting it had (dark mode, notifications, tone,
   sound effects, ambient sounds, theme, background, transparency, delete animation [legacy key snapChar]); this file only adds:
   - the sections that open in place (Notifications, Audio, Colors and Themes, Animation) and their one-line state text,
   - Menu transition speed: localStorage bc-anim-speed, applied by the shared window.bcMotion.applySpeed() (motion.js),
     plus the small looping preview, which pauses when its section is closed, the menu is closed, it is off-screen or the tab is hidden,
   - the Delete animation preview (transform/opacity only, pointer-events none, no layout shift),
   - the version beside Patch notes, read from the header version link (the one place a release sets it). */
(function () {
  "use strict";
  var root = document.documentElement, $ = function (id) { return document.getElementById(id); };
  var menu = $("app-menu");
  if (!menu || !$("menu-anim")) return;
  var RM = matchMedia("(prefers-reduced-motion: reduce)");

  // ---- Sections that open in place. Escape inside an open section closes just that section (inside Animation, that is
  // Animation itself: the Delete animations picker is part of it, beta.8).
  var sections = [["notify-head", "notify-panel"], ["audio-head", "audio-panel"], ["themes-head", "themes-panel"], ["anim-head", "anim-panel"], ["server-head", "switch-server-panel"]];
  sections.forEach(function (a) {
    var h = $(a[0]), pn = $(a[1]); if (!h || !pn) return;
    function set(on) { h.setAttribute("aria-expanded", on ? "true" : "false"); pn.hidden = !on; }
    h.addEventListener("click", function () { set(pn.hidden); });
    pn.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.stopPropagation(); set(false); h.focus(); } });
    set(false);
  });

  // ---- Below the banner (beta.8): the menu never overlaps the top banner. Its top edge is the banner's bottom edge,
  // measured live (safe-area inset included, since it is part of the header's box) into --bc-banner-bottom; its height is
  // capped to the rest of the viewport in menu.css, and it scrolls inside. The banner is the page header, or in a phone chat
  // (header folded to 0 height) the chat's top bar #thread-head. --bc-menu-right keeps its right edge under the burger.
  var header = document.querySelector("body > header"), threadHead = $("thread-head");
  function shown(el) { if (!el) return null; var r = el.getBoundingClientRect(); return r.height > 1 && r.bottom > 0 && getComputedStyle(el).display !== "none" && getComputedStyle(el).visibility !== "hidden" ? r : null; }
  function placeBelowBanner() {
    var hr = shown(header), tr = hr ? null : shown(threadHead), br = hr || tr;
    var bottom = br ? Math.max(0, Math.ceil(br.bottom)) : 0;
    var burger = hr ? $("menu-open") : $("chat-menu"), b = shown(burger);
    var vw = document.documentElement.clientWidth || innerWidth;
    var right = b ? Math.max(8, Math.round(vw - b.right)) : 8;
    root.style.setProperty("--bc-banner-bottom", bottom + "px");
    root.style.setProperty("--bc-menu-right", right + "px");
  }
  var placeRaf = 0;
  function schedulePlace() { if (!placeRaf) placeRaf = requestAnimationFrame(function () { placeRaf = 0; placeBelowBanner(); }); }
  placeBelowBanner();
  new MutationObserver(function () { if (!menu.hidden) placeBelowBanner(); }).observe(menu, {attributes: true, attributeFilter: ["hidden"]});
  addEventListener("resize", schedulePlace, {passive: true});
  if (window.visualViewport) visualViewport.addEventListener("resize", schedulePlace, {passive: true});
  addEventListener("scroll", function () { if (!menu.hidden) schedulePlace(); }, {passive: true, capture: true});
  if (window.ResizeObserver) { var ro = new ResizeObserver(schedulePlace); if (header) ro.observe(header); if (threadHead) ro.observe(threadHead); }
  addEventListener("load", schedulePlace); if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedulePlace);   // web fonts can change the banner height
  window.__bcPlaceMenu = placeBelowBanner;

  // ---- Reveal on expand: one shared handler for every section that opens in place (Notifications, Audio,
  // Colors and Themes, Animation, and the Ambient sounds options). When one opens, the menu scrolls just enough to show
  // the whole section; a section taller than the menu gets its header aligned to the top instead, so the header never
  // scrolls away. Smooth, lasting --bc-dur-menu (240 ms x --bc-anim-scale); instant under reduced motion. Never on
  // collapse, never when the section is already fully visible. A touch, wheel or key press cancels a running scroll.
  var EDGE = 8, revealRun = null;
  function stopReveal() { if (revealRun) { cancelAnimationFrame(revealRun); revealRun = null; } }
  ["wheel", "touchstart", "pointerdown", "keydown"].forEach(function (t) { menu.addEventListener(t, stopReveal, {passive: true}); });
  function revealSection(sec) {
    if (!sec || menu.hidden || menu.scrollHeight <= menu.clientHeight + 1) return;
    var m = menu.getBoundingClientRect(), r = sec.getBoundingClientRect();
    var cs = getComputedStyle(menu), bt = parseFloat(cs.borderTopWidth) || 0;
    var viewTop = Math.max(m.top + bt, 0) + EDGE;                                         // the menu can sit partly off-screen on a phone
    var viewBot = Math.min(m.top + bt + menu.clientHeight, window.innerHeight) - EDGE;
    if (r.top >= viewTop - 0.5 && r.bottom <= viewBot + 0.5) return;                      // already fully visible
    var delta = r.height > viewBot - viewTop ? r.top - viewTop                             // taller than the menu: header to the top
      : (r.bottom > viewBot ? r.bottom - viewBot : r.top - viewTop);                       // otherwise just enough
    var from = menu.scrollTop, to = Math.max(0, Math.min(menu.scrollHeight - menu.clientHeight, from + delta));
    if (Math.abs(to - from) < 1) return;
    stopReveal();
    if (RM.matches) { menu.scrollTop = to; return; }
    var dur = 240 * scale(), t0 = performance.now();                                      // beta.8: clock starts now, so the first frame already moves (WebKit under load)
    var down = to > from;
    function retarget() {                                                                  // beta.8: re-aim every frame, so content that settles while
      var m2 = menu.getBoundingClientRect(), r2 = sec.getBoundingClientRect(), st = menu.scrollTop;   // the section opens (WebKit) still ends "just enough"
      var vT = Math.max(m2.top + bt, 0) + EDGE, vB = Math.min(m2.top + bt + menu.clientHeight, window.innerHeight) - EDGE;
      var d = (r2.height > vB - vT || !down) ? r2.top - vT : r2.bottom - vB;
      return Math.max(0, Math.min(menu.scrollHeight - menu.clientHeight, st + d));
    }
    var step = function (now) {
      var k = Math.max(0, Math.min(1, (now - t0) / dur)), e = 1 - Math.pow(1 - k, 3);                   // ease-out, like --bc-ease-out
      to = retarget();
      menu.scrollTop = from + (to - from) * e;
      revealRun = k < 1 && !menu.hidden ? requestAnimationFrame(step) : null;
    };
    revealRun = requestAnimationFrame(step);
  }
  window.__bcReveal = revealSection;
  var revealMo = new MutationObserver(function (recs) {
    recs.forEach(function (rc) {
      var el = rc.target;
      if (el.hidden || rc.oldValue === null) return;                                       // only hidden -> shown
      var sec = el.closest("#menu-ambient") || el.closest(".notify-acc") || el;
      requestAnimationFrame(function () { revealSection(sec); });                           // after layout; runs alongside the expand animation
    });
  });
  document.querySelectorAll("#app-menu .notify-panel, #ambient-opts").forEach(function (pn) {
    revealMo.observe(pn, {attributes: true, attributeFilter: ["hidden"], attributeOldValue: true});
  });

  // ---- One-line state text on the Audio and Colors and Themes rows (Notifications is painted by app.js paintNotify).
  var aState = $("audio-state"), tone = $("notify-tone"), vol = $("tone-vol");
  function audioState() {
    if (!aState || !tone || !vol) return;
    var opt = tone.options[tone.selectedIndex];
    aState.textContent = tone.value === "off" ? "Tone off" : (opt ? opt.text : "Chime") + " \u00b7 " + vol.value + "%";
  }
  if (tone) tone.addEventListener("change", audioState);
  if (vol) vol.addEventListener("input", audioState);
  var tState = $("themes-state");
  function themesState() {
    if (!tState) return;
    var b = document.querySelector('#palette-choices .palette-btn[data-palette="' + (root.dataset.palette || "huzz") + '"]');
    tState.textContent = b ? b.textContent.trim() : "";
  }
  new MutationObserver(themesState).observe(root, {attributes: true, attributeFilter: ["data-palette"]});
  // app.js can change the tone after load (account prefs) without an event, so repaint whenever the menu opens.
  new MutationObserver(function () { if (!menu.hidden) { audioState(); themesState(); } }).observe(menu, {attributes: true, attributeFilter: ["hidden"]});
  audioState(); themesState();

  // ---- Version beside Patch notes: always the header version, styled as a link, both open the changelog.
  var mv = $("menu-ver"), hv = document.querySelector("header a.ver, header .ver");
  function showVer() {
    var v = hv && hv.textContent.trim().replace(/^v/i, "");
    if (!mv || !v) return;
    mv.textContent = "v" + v; mv.setAttribute("aria-label", "Version " + v.replace(/-beta\./, " beta ") + ", view patch notes"); mv.hidden = false;
  }
  showVer();
  if (hv) new MutationObserver(showVer).observe(hv, {childList: true, characterData: true, subtree: true});

  // ---- Menu transition speed (5 steps). bc-anim-speed is read by motion.js; --bc-anim-scale = 1 / speed scales every transition.
  var SPEED_TXT = {"0.25": "\u00bc\u00d7", "0.5": "\u00bd\u00d7", "1": "1\u00d7", "2": "2\u00d7", "4": "4\u00d7"};
  function scale() { var v = parseFloat(getComputedStyle(root).getPropertyValue("--bc-anim-scale")); return v > 0 ? v : 1; }
  var animState = $("anim-state"), demoSpeed = $("anim-demo-speed"), animPanel = $("anim-panel");
  var demo = $("anim-demo"), demoMenu = demo && demo.querySelector(".anim-demo-menu"), demoAnim = null, demoVisible = true;
  function demoShouldRun() { return !!demo && !RM.matches && !menu.hidden && animPanel && !animPanel.hidden && demoVisible && !document.hidden; }
  function buildDemo() {
    if (!demoMenu || !demoMenu.animate) return;
    if (demoAnim) demoAnim.cancel();
    demoAnim = null;
    if (RM.matches) { demoMenu.style.opacity = "1"; return; }
    demoMenu.style.opacity = "";
    var k = scale(), tin = 300 * k, hold = 600, tout = 260 * k, gap = 400, D = tin + hold + tout + gap;
    demoAnim = demoMenu.animate([
      {offset: 0, opacity: 0, transform: "translateY(-10px) scale(.96)"},
      {offset: tin / D, opacity: 1, transform: "none", easing: "ease-in"},
      {offset: (tin + hold) / D, opacity: 1, transform: "none"},
      {offset: (tin + hold + tout) / D, opacity: 0, transform: "translateY(-6px) scale(.98)"},
      {offset: 1, opacity: 0, transform: "translateY(-10px) scale(.96)"}
    ], {duration: D, iterations: Infinity, easing: "linear"});
    demoAnim.id = "anim-demo";
    syncDemo();
  }
  function syncDemo() {
    if (!demoMenu) return;
    if (RM.matches) { if (demoAnim) { demoAnim.cancel(); demoAnim = null; } demoMenu.style.opacity = "1"; return; }
    if (!demoAnim) { buildDemo(); return; }
    if (demoShouldRun()) { if (demoAnim.playState !== "running") demoAnim.play(); } else if (demoAnim.playState === "running") demoAnim.pause();
  }
  function paintSpeed(v) {
    if (animState) animState.textContent = SPEED_TXT[v] + (v === "1" ? " Normal" : "");
    if (demoSpeed) demoSpeed.textContent = SPEED_TXT[v];
    var r = document.querySelector('input[name="anim-speed"][value="' + v + '"]'); if (r && !r.checked) r.checked = true;
  }
  function setSpeed(v) {
    v = String(v); if (!SPEED_TXT[v]) v = "1";
    try { localStorage.setItem("bc-anim-speed", v); } catch (e) {}
    if (window.bcMotion && window.bcMotion.applySpeed) window.bcMotion.applySpeed();
    paintSpeed(v); buildDemo();
  }
  function storedSpeed() {
    var s = 1; try { s = parseFloat(localStorage.getItem("bc-anim-speed") || "1"); } catch (e) {}
    var best = "1"; Object.keys(SPEED_TXT).forEach(function (k) { if (Math.abs(parseFloat(k) - s) < Math.abs(parseFloat(best) - s)) best = k; });
    return best;
  }
  document.querySelectorAll('input[name="anim-speed"]').forEach(function (r) { r.addEventListener("change", function () { if (r.checked) setSpeed(r.value); }); });
  paintSpeed(storedSpeed()); buildDemo();
  addEventListener("storage", function (e) { if (e.key === "bc-anim-speed") { paintSpeed(storedSpeed()); buildDemo(); } });
  var mo = new MutationObserver(syncDemo);
  mo.observe(menu, {attributes: true, attributeFilter: ["hidden"]});
  if (animPanel) mo.observe(animPanel, {attributes: true, attributeFilter: ["hidden"]});
  if ("IntersectionObserver" in window && demo) new IntersectionObserver(function (es) { demoVisible = es[es.length - 1].isIntersecting; syncDemo(); }).observe(demo);
  document.addEventListener("visibilitychange", syncDemo);
  if (RM.addEventListener) RM.addEventListener("change", buildDemo);
  window.__bcDemo = function () { return demoAnim; };
  (function () {   // keyboard focus ring for the speed row in every engine (WebKit skips :focus-visible after arrow-key radio moves)
    var g = $("anim-speed"); if (!g) return;
    g.addEventListener("keydown", function () { g.classList.add("kb"); });
    g.addEventListener("pointerdown", function () { g.classList.remove("kb"); });
    g.addEventListener("focusout", function (e) { if (!g.contains(e.relatedTarget)) g.classList.remove("kb"); });
  })();

  // ---- Delete animation preview (legacy "spell"/"snap" names in code): a mini of the tapped character casts at the "Delete animations" heading, which nearly
  // gets deleted, then bounces back. ~1150 ms at 1x, scaled by the speed setting; a static mini for ~0.7 s under reduced motion.
  var layer = $("snap-spell-layer"), label = $("snap-label");
  // How big the preview character is, as a multiple of the original 44x35 px mini. the owner chose 3 (triple size) on Oct 18 2026;
  // change this one number to resize it. (For side-by-side checks, localStorage "bc-spell-size" = "2" or "3" overrides it on one device.)
  var SPELL_SIZE = 3;
  try { var spellOv = +localStorage.getItem("bc-spell-size"); if (spellOv === 1 || spellOv === 2 || spellOv === 3) SPELL_SIZE = spellOv; } catch (e) {}
  var spellAnims = [], ART = ["treant", "fox", "owl", "badger", "bunny", "painter", "robot", "fisherman", "turtle", "crane", "snail", "otter", "beaver"], altN = 0;
  if (label && !label.querySelector(".sl-ch")) {   // split once at load, so nothing reflows during the effect
    var labTxt = label.textContent; label.textContent = ""; label.setAttribute("aria-label", labTxt);
    labTxt.split("").forEach(function (ch) { var sp = document.createElement("span"); sp.className = "sl-ch"; sp.setAttribute("aria-hidden", "true"); sp.textContent = ch; label.appendChild(sp); });
  }
  function clearSpell() { clearSpell.tok = null; spellAnims.forEach(function (a) { try { a.cancel(); } catch (e) {} }); spellAnims = []; if (layer) layer.textContent = ""; }
  function A(el, kf, o) { var a = el.animate(kf, o); a.id = "spell"; spellAnims.push(a); return a; }
  function castSpell(kind, btn) {
    if (!layer || !label || !document.body.animate) return;
    clearSpell();
    var m = menu.getBoundingClientRect(), b = btn.getBoundingClientRect(), L = label.getBoundingClientRect();
    var S = SPELL_SIZE, W = 44 * S, H = 35 * S, pad = 10, vh = window.innerHeight;
    W = Math.min(W, Math.max(44, m.width * .4)); S = W / 44; H = 35 * S;   // never more than ~40% of a narrow menu, so it can't spill sideways
    var bottom = Math.min(m.bottom, vh) - pad - H;
    var right = m.right - pad - W, left = m.left + pad;
    var useLeft = b.right > right - 6 && b.bottom > bottom - 6;   // bottom-right unless the tapped control sits in that corner
    var x = useLeft ? left : right, y = bottom;
    var mini = document.createElement("div"); mini.className = "spell-mini"; mini.style.left = x + "px"; mini.style.top = y + "px";
    mini.style.width = W + "px"; mini.style.height = H + "px";   // the CSS size is the 1x one
    mini.dataset.char = kind; mini.dataset.corner = useLeft ? "bottom-left" : "bottom-right";
    var img = document.createElement("img"); img.src = "/static/snap/" + kind + ".svg?v=5"; img.alt = ""; mini.appendChild(img);
    if (useLeft) img.style.transform = "scaleX(-1)";
    var wand = document.createElement("span"); wand.className = "spell-wand";
    wand.style.width = 14 * S + "px"; wand.style.height = 2.5 * S + "px"; wand.style.top = 6 * S + "px";   // the wand grows with the character
    if (!useLeft) { wand.style.left = -10 * S + "px"; wand.style.transformOrigin = "100% 50%"; } else { wand.style.left = 40 * S + "px"; }
    mini.appendChild(wand); layer.appendChild(mini);
    if (RM.matches) {   // reduced motion: tiny static confirmation, no movement
      mini.style.opacity = "1"; mini.classList.add("static");
      var tok = {}; clearSpell.tok = tok;
      setTimeout(function () { if (clearSpell.tok === tok && mini.isConnected) mini.remove(); }, 700);
      return;
    }
    var k = scale(), T = function (ms) { return ms * k; };
    A(mini, [
      {offset: 0, opacity: 0, transform: "translateY(" + 8 * S + "px) scale(.3)"},
      {offset: 180 / 1150, opacity: 1, transform: "translateY(" + -3 * S + "px) scale(1.12)"},
      {offset: 260 / 1150, opacity: 1, transform: "none"},
      {offset: 330 / 1150, opacity: 1, transform: "rotate(" + (useLeft ? 10 : -10) + "deg)"},
      {offset: 420 / 1150, opacity: 1, transform: "rotate(" + (useLeft ? -6 : 6) + "deg)"},
      {offset: 520 / 1150, opacity: 1, transform: "none"},
      {offset: 900 / 1150, opacity: 1, transform: "none"},
      {offset: 980 / 1150, opacity: 1, transform: "translateY(" + -6 * S + "px) scale(1.05)"},
      {offset: 1, opacity: 0, transform: "translateY(" + 4 * S + "px) scale(.2)"}
    ], {duration: T(1150), fill: "both"});
    A(wand, [{opacity: 0, transform: "rotate(" + (useLeft ? -40 : 40) + "deg)"}, {opacity: 1, transform: "rotate(" + (useLeft ? 25 : -25) + "deg)", offset: .45},
      {opacity: 1, transform: "rotate(" + (useLeft ? -10 : 10) + "deg)", offset: .8}, {opacity: 0, transform: "rotate(0deg)"}], {duration: T(300), delay: T(220), fill: "both", easing: "ease-in-out"});
    var sx = useLeft ? x + W + 4 * S : x - 8 * S, sy = y + 6 * S, sk = Math.sqrt(S);   // sparks leave from the wand tip; a bit bigger too
    var tx = L.left + L.width * .5, ty = L.top + L.height * .5;
    for (var i = 0; i < 7; i++) {
      var sp = document.createElement("span"); sp.className = "spell-spark" + (i % 3 === 0 ? " star" : ""); sp.style.left = sx + "px"; sp.style.top = sy + "px"; if (sk > 1) { sp.style.width = sp.style.height = 9 * sk + "px"; sp.style.margin = -4.5 * sk + "px 0 0 " + -4.5 * sk + "px"; } layer.appendChild(sp);
      var dx = tx - sx + (i - 3) * 9, dy = ty - sy + ((i * 7) % 5 - 2) * 3, bend = -40 - (i % 3) * 18;
      A(sp, [{opacity: 0, transform: "translate(0,0) scale(.4)"}, {opacity: 1, transform: "translate(" + dx * .45 + "px," + (dy * .45 + bend) + "px) scale(1.15)", offset: .5},
        {opacity: .9, transform: "translate(" + dx + "px," + dy + "px) scale(.8)", offset: .88}, {opacity: 0, transform: "translate(" + dx + "px," + dy + "px) scale(.2)"}],
        {duration: T(380), delay: T(300 + i * 28), fill: "both", easing: "cubic-bezier(.3,.7,.4,1)"});
    }
    label.querySelectorAll(".sl-ch").forEach(function (c, j) {
      var r = ((j * 37) % 11 - 5), up = -4 - (j * 13) % 7;
      A(c, [{transform: "none", opacity: 1}, {offset: .1, transform: "none", opacity: 1},
        {offset: .5, transform: "translate(" + r * 1.4 + "px," + up + "px) rotate(" + r * 6 + "deg) scale(.85)", opacity: .15},
        {offset: .62, transform: "translate(" + r * 1.6 + "px," + (up - 1) + "px) rotate(" + r * 7 + "deg) scale(.8)", opacity: .1},
        {offset: .82, transform: "translateY(-3px) scale(1.12)", opacity: 1}, {offset: .92, transform: "translateY(1px) scale(.97)", opacity: 1}, {transform: "none", opacity: 1}],
        {duration: T(420), delay: T(580 + (j % 4) * 10), fill: "both", easing: "ease-in-out"});
    });
    var first = spellAnims[0];
    first.finished.then(function () { if (spellAnims.indexOf(first) >= 0) clearSpell(); }).catch(function () {});
  }
  // The menu closing mid-spell clears it at once.
  new MutationObserver(function () { if (menu.hidden) clearSpell(); }).observe(menu, {attributes: true, attributeFilter: ["hidden"]});
  document.querySelectorAll("#snapchar-choices [data-snapchar]").forEach(function (b) {
    b.addEventListener("click", function () {   // app.js saves the choice; this only plays the preview. Take turns cycles the characters.
      var k = b.dataset.snapchar; if (k === "off") return; if (k === "alt") { k = ART[altN % ART.length]; altN++; }
      if (ART.indexOf(k) < 0) k = "fox";
      castSpell(k, b);
    });
  });
  window.__bcSpell = function () { return spellAnims; };
})();
