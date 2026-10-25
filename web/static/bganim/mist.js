/* Mist: the mist banks drift and breathe, and the trees sway in a staggered breeze.
   Added Oct 19 2026 for 26.10.4 (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/mist/gen_mist.py).
   Everything is described in mist/<scheme>-<size>/parts.js and played by the shared helpers in anim2.js. */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.mist = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base) };
})();
