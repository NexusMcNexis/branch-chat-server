/* Dusk: the clouds drift slowly, the sun glow (light) or moon halo (dark) breathes, a few star groups dim and brighten (dark), and the treeline and edge trees sway in a staggered breeze.
   Added Oct 19 2026 for 26.10.4 (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/dusk/gen_dusk.py).
   Everything is described in dusk/<scheme>-<size>/parts.js and played by the shared helpers in anim2.js. */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.dusk = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base) };
})();
