/* Branch Chat motion (v0.23.3-beta.4): the close half of the shared pop-up transitions (motion.css plays the open half).
   When a menu ([data-bc-motion], toggled with the hidden attribute) or a <dialog> closes, the app's state changes at
   once exactly as before (hidden = true / dialog.close(), focus returns, inert lifts, Escape and outside-tap behave the
   same). This file only keeps the element painted for the close animation: .bc-m-leave, aria-hidden, no pointer
   events, and for a dialog a fixed copy of its last on-screen box plus a fading backdrop. Reopening mid-fade cancels it.
   Elements that sit in the page flow (the emoji panel, the friend list) close instantly so nothing jumps afterwards.
   Public: window.bcMotion.register(el), .isLeaving(el), .afterLeave(el, fn). */
(function () {
  "use strict";
  // Animation speed (setting arrives with the menu reorganization): localStorage bc-anim-speed holds a speed multiplier
  // (0.25, 0.5, 1, 2, 4; higher = faster), clamped to 0.25-4, default 1.
  // Durations are multiplied by --bc-anim-scale = 1 / speed. Reduced motion ignores it (motion.css).
  function applySpeed() {
    let speed = 1;
    try { speed = parseFloat(localStorage.getItem("bc-anim-speed") || "1"); } catch (err) {}
    if (!isFinite(speed) || speed <= 0) speed = 1;
    speed = Math.min(4, Math.max(0.25, speed));
    try { document.documentElement.style.setProperty("--bc-anim-scale", String(+(1 / speed).toFixed(4))); } catch (err) {}
    return speed;
  }
  applySpeed();
  addEventListener("storage", (ev) => { if (ev.key === "bc-anim-speed") applySpeed(); });
  if (window.bcMotion || typeof MutationObserver === "undefined") return;
  const S = new WeakMap();
  const shown = (el) => (el.tagName === "DIALOG" ? el.open : !el.hidden);
  const st = (el) => {
    let s = S.get(el);
    if (!s) { s = {visible: shown(el), display: "", rect: null, scroll: 0, backdrop: "", modal: false, leaving: null}; S.set(el, s); }
    return s;
  };
  const ms = (v) => Math.max(0, ...String(v || "0s").split(",").map((t) => (t.trim().endsWith("ms") ? parseFloat(t) : parseFloat(t) * 1000) || 0));

  function measure(el) {
    // The untransformed box, centred where it is drawn (the open animation scales around the centre).
    const s = st(el);
    try {
      const cs = getComputedStyle(el);
      if (cs.display !== "none") s.display = cs.display;
      if (el.tagName === "DIALOG" && el.open) {
        const r = el.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
        if (w && h) s.rect = {left: r.left + r.width / 2 - w / 2, top: r.top + r.height / 2 - h / 2, width: w, height: h};
        try { s.modal = el.matches(":modal"); } catch (err) { s.modal = true; }
        try { s.backdrop = getComputedStyle(el, "::backdrop").backgroundColor || ""; } catch (err) { s.backdrop = ""; }
      }
    } catch (err) {}
  }

  function endLeave(el) {
    const s = st(el), L = s.leaving;
    if (!L) return;
    s.leaving = null;
    clearTimeout(L.timer);
    el.removeEventListener("animationend", L.onEnd);
    el.classList.remove("bc-m-leave");
    for (const [p, [v, pri]] of Object.entries(L.saved)) { if (v) el.style.setProperty(p, v, pri); else el.style.removeProperty(p); }
    if (L.ariaHidden === null) el.removeAttribute("aria-hidden"); else el.setAttribute("aria-hidden", L.ariaHidden);
    if (L.scrim) L.scrim.remove();
    for (const fn of L.after) { try { fn(); } catch (err) { setTimeout(() => { throw err; }); } }
  }

  function leave(el) {
    const s = st(el);
    if (!s.display || el.dataset.bcMotion === "none" || document.visibilityState === "hidden") return;
    const isDialog = el.tagName === "DIALOG";
    if (isDialog && !s.rect) return;
    let pos = "";
    try { pos = getComputedStyle(el).position; } catch (err) {}
    if (!isDialog && (pos === "static" || pos === "relative" || pos === "")) return;   // in the page flow: close at once
    // Longhands only, each saved before it is set, so endLeave() puts back exactly what the app had.
    const saved = {};
    const set = (p, v) => { if (!(p in saved)) saved[p] = [el.style.getPropertyValue(p), el.style.getPropertyPriority(p)]; el.style.setProperty(p, v, "important"); };
    set("display", isDialog ? "block" : s.display);
    let scrim = null;
    if (isDialog) {
      const r = s.rect;
      set("position", "fixed"); set("top", r.top + "px"); set("left", r.left + "px"); set("right", "auto"); set("bottom", "auto");
      set("width", r.width + "px"); set("height", r.height + "px"); set("margin", "0px"); set("box-sizing", "border-box");
      set("max-width", "none"); set("max-height", "none"); set("min-width", "0px"); set("min-height", "0px"); set("z-index", "2147482000");
      let otherModal = false;
      try { otherModal = !!document.querySelector("dialog:modal"); } catch (err) { otherModal = !!document.querySelector("dialog[open]"); }
      if (s.modal && !otherModal) {
        scrim = document.createElement("div");
        scrim.className = "bc-m-scrim";
        scrim.setAttribute("aria-hidden", "true");
        if (s.backdrop && s.backdrop !== "rgba(0, 0, 0, 0)") scrim.style.background = s.backdrop;
        document.body.append(scrim);
      }
    }
    // If the browser could not hand focus back (e.g. the opener is gone), focus would otherwise stay in the fading copy.
    const a = document.activeElement;
    if (a && a !== document.body && el.contains(a)) { try { a.blur(); } catch (err) {} }
    const L = {saved, scrim, after: [], ariaHidden: el.getAttribute("aria-hidden"), timer: 0, onEnd: null};
    s.leaving = L;
    el.setAttribute("aria-hidden", "true");
    el.classList.add("bc-m-leave");
    if (s.scroll) { try { el.scrollTop = s.scroll; } catch (err) {} }
    let cs = null;
    try { cs = getComputedStyle(el); } catch (err) {}
    const dur = cs && cs.animationName !== "none" ? ms(cs.animationDuration) + ms(cs.animationDelay) : 0;
    if (!dur) { endLeave(el); return; }
    L.onEnd = (ev) => { if (ev.target === el && String(ev.animationName).startsWith("bc-m-")) endLeave(el); };
    el.addEventListener("animationend", L.onEnd);
    L.timer = setTimeout(() => endLeave(el), dur + 120);   // backstop if animationend never comes
  }

  function sync(el) {
    const s = st(el), now = shown(el);
    if (now) {
      if (s.leaving) endLeave(el);
      if (!s.visible) { s.visible = true; measure(el); }
      return;
    }
    if (!s.visible) return;
    s.visible = false;
    leave(el);
  }

  const mo = new MutationObserver((records) => { for (const el of new Set(records.map((r) => r.target))) sync(el); });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver((entries) => { for (const e of entries) if (shown(e.target)) measure(e.target); }) : null;

  function register(el) {
    if (!el || S.has(el) && S.get(el).registered) return;
    const s = st(el);
    s.registered = true;
    s.visible = shown(el);
    if (s.visible) measure(el);
    mo.observe(el, {attributes: true, attributeFilter: el.tagName === "DIALOG" ? ["open"] : ["hidden"]});
    el.addEventListener("scroll", () => { if (!st(el).leaving) st(el).scroll = el.scrollTop; }, {passive: true});
    el.addEventListener("animationend", (ev) => { if (ev.target === el && shown(el)) measure(el); });
    if (el.tagName === "DIALOG") {
      if (ro) ro.observe(el);
      el.addEventListener("cancel", () => measure(el), true);   // Escape: measure before the browser closes it
    }
  }

  // dialog.close() still closes synchronously; this only records the box a moment before.
  try {
    const proto = HTMLDialogElement.prototype, close = proto.close;
    if (typeof close === "function" && !close.bcMotion) {
      const wrapped = function () { if (this.open && S.has(this)) measure(this); return close.apply(this, arguments); };
      wrapped.bcMotion = true;
      proto.close = wrapped;
    }
  } catch (err) {}
  addEventListener("resize", () => { for (const d of document.querySelectorAll("dialog[open]")) if (S.has(d)) measure(d); }, {passive: true});

  // Length of an element's current transition/animation in ms (already scaled), for code that waits on one.
  const durationOf = (el, which) => {
    try { const cs = getComputedStyle(el); return which === "transition" ? ms(cs.transitionDuration) + ms(cs.transitionDelay) : ms(cs.animationDuration) + ms(cs.animationDelay); } catch (err) { return 0; }
  };
  window.bcMotion = {
    register,
    applySpeed,
    durationOf,
    isLeaving: (el) => !!(el && S.has(el) && S.get(el).leaving),
    afterLeave(el, fn) { const L = el && S.has(el) && S.get(el).leaving; if (L) L.after.push(fn); else fn(); },
  };
  const init = () => document.querySelectorAll("dialog, [data-bc-motion]").forEach(register);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
