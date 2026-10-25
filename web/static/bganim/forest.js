/* Forest camp scene: the moving version of BranchChat's DEFAULT wallpaper (the campfire, tent, stream and forest picture
   that shows when a chat has no background of its own, and always in the Lobby). Added Oct 19 2026 after Luke's call.
   Three things move, everything else stays put:
     1. Wind   - the ferns, the stream bank and the tree tops lean gently in a light breeze. Each band of foliage gets its
                 own timing (the shared staggered sway in bg-anim.js), so nothing moves in lockstep.
     2. Fire   - the flames stretch and squeeze quickly about their base, a brighter copy of the flames flickers over them,
                 the warm glow around the fire breathes, small embers float up and fade, and three thin smoke wisps rise
                 and drift with the wind.
     3. Stream - small soft glints twinkle on the bright water and slide a little downstream, so the water looks like it
                 is flowing. Kept faint on purpose.
   No sound: a fire-crackle sound is a separate item waiting for Luke's approval and is NOT part of this background.
   Files: bganim/forest/<scheme>-<size>/parts.js (plain JSON, named .js because the app server only serves known file
   types) and the pictures next to it, made by /workspace/default-bg-anim/gen_forest_default.py from the live wallpaper
   files (forest-bg.webp wide, forest-tall.webp tall; dark = the same picture at the wallpaper's 85 % brightness).
   Like every scene the loop is 60 s and starts and ends exactly on the still: at t = 0 and t = 60 every cut piece is at
   rest and every sprite (ember, smoke, glint, glow, flicker) is invisible; the sprites fade in over the first 3 s and out
   over the last 3 s. Uses the shared helpers in anim2.js (BgMotion); nothing here changes the shared runtime. */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.forest = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base, (def, c) => {
    const M = window.BgMotion, j = c.j, L = window.BgAnim.L, TAU = Math.PI * 2, R = j.ramp_s || 3;
    const [FX, FY] = j.fire;
    // The plain wallpaper is already painted right underneath this layer and is the same picture, so the full-size still
    // does not need to be drawn again on every frame. Dropping it leaves only the small moving pieces to draw, which is what
    // keeps Safari at full speed on big screens (with it, Safari on the test box fell to about 30 fps on a desktop window).
    def.parts = def.parts.filter((p) => !p.src.endsWith('/' + j.still));
    const fade = (t) => M.env(t, 0, L, R, R);            // 0 at the very start and end of the loop, 1 in between
    const frac = (x) => x - Math.floor(x);
    // the brighter flame copy flickers (fast, uneven), the glow breathes slowly; both start and end invisible
    for (const p of def.parts) {
      if (p.role === 'flicker') { p.initial = { opacity: 0 }; p.keyframes = () => M.build((t) => ({ op: fade(t) * (0.15 + 0.2 * M.osc(t, 1.2) + 0.15 * M.osc(t, 0.75) + 0.1 * M.osc(t, 2)) })); }
      if (p.role === 'glow') { p.initial = { opacity: 0 }; p.keyframes = () => M.build((t) => ({ op: fade(t) * (scheme === 'dark' ? 0.3 : 0.2) * (0.7 + 0.2 * M.osc(t, 4) + 0.1 * M.osc(t, 1.5)) })); }
    }
    // embers: each one rises from the flames k times a loop, drifting and swaying, brightest midway, then fades out.
    // When it restarts at the fire it is invisible, and build() jumps it there in one step (wrap key).
    for (const e of j.embers) {
      const U = (t) => e.u0 + e.k * M.trav(t);
      def.parts.push({ src: c.url('ember-dot.webp'), x: FX - e.s / 2, y: FY - 40 - e.s / 2, w: e.s, h: e.s, initial: { opacity: 0 },
        keyframes: () => M.build((t) => { const u = frac(U(t));
          return { tx: e.x0 + e.xs * u + e.sw * Math.sin(TAU * t / e.sp) * u, ty: -u * e.D, op: fade(t) * e.amax * Math.pow(Math.sin(Math.PI * u), 0.8) }; },
          L, 0.1, (t) => Math.floor(U(t))) });
    }
    // smoke: three soft wisps rise from the fire, grow wider and fainter, and lean with the wind
    for (const s of j.smoke) {
      const U = (t) => s.u0 + s.k * M.trav(t);
      def.parts.push({ src: c.url('smoke.webp'), x: FX - 45, y: FY - 90 - 260, w: 90, h: 260, origin: '50% 100%', initial: { opacity: 0 },
        keyframes: () => M.build((t) => { const u = frac(U(t));
          return { tx: s.x0 + s.drift * u, ty: -u * s.D * 0.5, sx: 0.7 + 0.6 * u, sy: 0.6 + 0.5 * u, op: fade(t) * s.amax * Math.sin(Math.PI * u) }; },
          L, 0.25, (t) => Math.floor(U(t))) });
    }
    // stream glints: twinkle k times a loop, sliding a few pixels downstream while lit, invisible when they restart
    for (const g of j.glints) {
      const U = (t) => g.u0 + g.k * t / L;
      def.parts.push({ src: c.url('glint.webp'), x: g.x - g.s / 2, y: g.y - g.s / 2, w: g.s, h: g.s, initial: { opacity: 0 },
        keyframes: () => M.build((t) => { const u = frac(U(t));
          return { tx: g.dx * u, ty: g.dy * u, op: fade(t) * g.amax * Math.pow(Math.sin(Math.PI * u), 3) }; },
          L, 0.1, (t) => Math.floor(U(t))) });
    }
  }) };
})();
