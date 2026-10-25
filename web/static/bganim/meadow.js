/* Meadow: the grass and tree clumps sway in a staggered breeze, and the clouds drift a little (light).
   Added Oct 19 2026 for 26.10.4 (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/meadow/gen_meadow.py).
   Everything is described in meadow/<scheme>-<size>/parts.js and played by the shared helpers in anim2.js. */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.meadow = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base) };
})();
