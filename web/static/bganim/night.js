/* Night: the ridge trees sway a little, the sky glow breathes and slow clouds travel; in dark mode a few constellations
   draw themselves star to star, hold, and fade, and one shooting star crosses once per loop. The painting is the Night
   still with the distant catch tower at the water's edge (night-tower "shore v2", Oct 18 2:14 AM; approved by Luke
   Oct 19 2:15 AM). The tower itself stands still; no moving part touches it. The Starship launch/catch sequence from the
   spec was never built, so it is not here.
   Added Oct 19 2026 for 26.10.4 (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/night/gen_night.py). */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.night = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base, (def, c) => {
    const M = window.BgMotion, j = c.j, L = window.BgAnim.L;
    if (!j.constellations) return;   // light mode: only the gentle motion from parts.js
    const TH = 7;   // line thickness in picture pixels (about 1 screen pixel of core with a soft glow)
    j.constellations.forEach((cn, ci) => {
      const [a, b, h, f] = j.schedule[ci], n = cn.lines.length, seg = (b - a) / n;   // draw from a to b, hold to h, fade out by f
      cn.lines.forEach(([i0, i1], k) => {
        const [x1, y1] = cn.stars[i0], [x2, y2] = cn.stars[i1], len = Math.hypot(x2 - x1, y2 - y1), ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
        const s0 = a + k * seg;
        def.parts.push({ src: c.url('cline.webp'), x: x1, y: y1 - TH / 2, w: len, h: TH, origin: '0% 50%', initial: { opacity: 0 },
          keyframes: () => M.build((t) => {
            const p = M.ease((t - s0) / seg), fade = t < h ? 1 : 1 - M.ease((t - h) / (f - h));
            return { rot: ang, sx: Math.max(0.0001, p), op: t <= s0 || t >= f ? 0 : j.line_alpha * fade };
          }) });
      });
    });
    const s = j.shooting, ang = Math.atan2(s.dy, s.dx) * 180 / Math.PI;   // the shooting star
    def.parts.push({ src: c.url('shoot.webp'), x: s.x0 - s.len, y: s.y0 - 4.5, w: s.len, h: 9, origin: '100% 50%', initial: { opacity: 0 },
      keyframes: () => M.build((t) => {
        const q = (t - s.t0) / s.dur, e = q <= 0 || q >= 1 ? 0 : Math.sin(Math.PI * q), p = Math.max(0, Math.min(1, q));
        return { tx: s.dx * p, ty: s.dy * p, rot: ang, op: s.alpha * e, sx: 0.4 + 0.6 * Math.sin(Math.PI * p) };
      }, L, 0.05) });
  }) };
})();
