/* Tide: the water strips shimmer, the shore plants sway, and fish swim. One "home" fish is part of the still picture:
   over the first 3 s a copy of the plain painting fades over it while the swimming copy fades in on the very same spot,
   then it swims a slow loop and turns by narrowing at the far ends; three more fish cross the water at different times,
   starting and ending off-screen. The last 3 s do the reverse, so the loop ends exactly on the still.
   Added Oct 19 2026 for 26.10.4 (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/tide/gen_tide.py). */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.tide = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base, (def, c) => {
    const M = window.BgMotion, j = c.j, W = j.W, H = j.H, L = window.BgAnim.L, R = j.ramp_s, FL = j.fish_len, FH = j.fish_h, TAU = Math.PI * 2;
    const xf = (t) => M.env(t, 0, L, R, R);   // 0 at the very start and end of the loop, 1 in between
    // the plain-painting patch over the baked-in home fish fades in as the swimming fish takes over (and out at the end)
    const patch = def.parts.find((p) => p.role === 'patch');
    if (patch) { patch.initial = { opacity: 0 }; patch.keyframes = () => M.build((t) => ({ op: xf(t) })); }
    const jx = (14 - 42) / 84 * FL;   // where the tail joins the body, relative to the fish centre
    j.fish.forEach((f, i) => {
      let path;
      if (f.id === 'home') {   // a slow closed loop around its home spot; it turns by narrowing (scaleX) at the far points
        path = (t) => { const th = TAU * M.trav(t), c0 = Math.cos(th);
          return { x: f.home[0] * W + f.A * W * Math.sin(th), y: f.home[1] * H + f.B * H * (1 - Math.cos(2 * th)) / 2,
                   sx: Math.max(-1, Math.min(1, c0 / 0.35)), op: j.fish_alpha * xf(t), wagA: M.env(t, 0, L, 4, 4) }; };
      } else {                 // a fish crossing the water: off-screen before t0 and after t1
        const m = j.margin;
        path = (t) => { const s = Math.min(1, Math.max(0, (t - f.t0) / (f.t1 - f.t0))), p = s * 0.85 + 0.15 * (s - Math.sin(TAU * s) / TAU);
          const x = f.dir > 0 ? -m + p * (W + 2 * m) : W + m - p * (W + 2 * m);
          return { x, y: f.lane * H + 0.008 * H * Math.sin(TAU * s * 2), sx: f.dir, op: j.fish_alpha * (s > 0 && s < 1 ? 1 : 0), wagA: 1 }; };
      }
      const cx = f.id === 'home' ? f.home[0] * W : 0, cy = f.lane * H;
      // each fish is two pictures, body and tail; the tail wags about the joint
      const mk = (part) => ({ src: c.url(`fish${i}-${part}.webp`), x: cx - FL / 2, y: cy - FH / 2, w: FL, h: FH, origin: '50% 50%', initial: { opacity: 0 },
        keyframes: () => M.build((t) => { const P = path(t), wag = 9 * Math.sin(TAU * t / 1.5) * P.wagA;
          const v = { tx: P.x - cx, ty: P.y - cy, sx: P.sx, op: P.op };
          if (part === 'tail') v.post = `translate(${jx}px,0px) rotate(${wag.toFixed(3)}deg) translate(${-jx}px,0px)`;
          return v; }, L, 0.1) });
      def.parts.push(mk('tail')); def.parts.push(mk('body'));
    });
  }) };
})();
