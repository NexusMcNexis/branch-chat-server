/* Linen: a soft patch of light slowly drifts and brightens across the cloth, dust motes float in it, and once per loop
   a needle comes up through the cloth, sews three stitches along a seam and the thread is pulled fully through, so
   nothing is left by the end. The weave itself never moves (moving it by fractions of a pixel would shimmer).
   Starts and ends on the linen-fix v8 still (weave + folds; brighter dark). Added Oct 19 2026 for 26.10.4
   (built Oct 18 as preview anim-bg-2, mockups/animated-backgrounds/linen/gen_linen_anim.py). */
(function () {
  window.BgScenes = window.BgScenes || {};
  window.BgScenes.linen = { load: (scheme, size, base) => window.BgMotion.load(scheme, size, base, (def, c) => {
    const M = window.BgMotion, j = c.j, L = window.BgAnim.L;
    M.addMotes(def, c, j.motes, (t) => j.patch.cx + j.patch.drift * M.osc(t, 60), c.url('mote.webp'));
    const N = j.needle, rad = N.angle * Math.PI / 180, dx = Math.cos(rad), dy = Math.sin(rad);
    const P = (s) => [N.start[0] + dx * s, N.start[1] + dy * s];   // the point s pixels along the seam
    const stitches = [...Array(N.n).keys()].map((k) => { const a = k * (N.stitch + N.gap), t0 = N.t0 + k * (N.st_dur + N.under); return { a, b: a + N.stitch, t0, t1: t0 + N.st_dur }; });
    const tEnd = stitches[N.n - 1].t1;
    // each thread stitch grows behind the needle, then shrinks toward its far end as the thread is pulled through
    stitches.forEach((st, k) => {
      const [x1, y1] = P(st.a);
      def.parts.push({ src: c.url('thread.webp'), x: x1, y: y1 - N.thread_w / 2, w: N.stitch, h: N.thread_w, origin: '0% 50%', initial: { opacity: 0 },
        keyframes: () => M.build((t) => {
          const grow = M.ease((t - st.t0) / (st.t1 - st.t0));
          const pr0 = tEnd + k * N.pull, pull = M.ease((t - pr0) / N.pull);
          const len = Math.max(0.0001, grow * (1 - pull));
          return { rot: N.angle, tx: N.stitch * pull * dx, ty: N.stitch * pull * dy, sx: len, op: t < st.t0 || t > pr0 + N.pull ? 0 : N.thread_alpha };
        }, L, 0.1) });
    });
    // the needle rises at each stitch start, glides to its end and dips back under; after the last one it stays under
    def.parts.push({ src: c.url('needle.webp'), x: N.start[0] - N.len, y: N.start[1] - N.h / 2, w: N.len, h: N.h, origin: '100% 50%', initial: { opacity: 0 },
      keyframes: () => M.build((t) => {
        let s = 0, vis = 0;
        for (const st of stitches) {
          if (t >= st.t0 - 0.01 && t <= st.t1 + 0.01) { const q = (t - st.t0) / (st.t1 - st.t0); s = st.a + M.ease(q) * (st.b - st.a); vis = Math.min(M.ease(q / 0.15), M.ease((1 - q) / 0.15)); }
          else if (t > st.t1) s = st.b;
        }
        const [x, y] = P(s);
        return { tx: x - N.start[0], ty: y - N.start[1], rot: N.angle, sx: 0.55 + 0.45 * vis, op: N.alpha * vis };
      }, L, 0.1) });
  }) };
})();
