/* Shared helpers for the "parts list" moving backgrounds (Dusk, Mist, Meadow, Tide, Linen, Night), added Oct 19 2026
   for 26.10.4. These scenes were built and checked as previews on Oct 18 (mockups/animated-backgrounds, job anim-bg-2)
   but were never switched on in the app; this file brings them in without changing the shared runtime (bg-anim.js).

   Each scene folder has <scheme>-<size>/parts.js: plain JSON (named .js because the app server only serves known file
   types) listing the pictures and how each one moves:
     sway    - trees and grass lean in the wind, about their base (uses the runtime's staggered BgAnim.sway, so no two
               trees move in lockstep)
     drift   - clouds or mist slide or grow a little and come back
     breathe - a glow fades up a little and back down
     travel  - something moves one full step along over the loop
     loop    - a small closed ellipse (a gentle bob)
   Every motion is at rest at t = 0 and t = 60 s, so the loop starts and ends exactly on the still picture.
   The moving pieces are cut from the still itself, so at t = 0 the stack of pieces is the still, pixel for pixel. */
(function () {
  const B = window.BgAnim, L = B.L, TAU = Math.PI * 2, DEG = 180 / Math.PI;

  // Small motion helpers, all at rest (value 0 and not moving) at t = 0 and t = L.
  const osc = (t, P) => (1 - Math.cos(TAU * t / P)) / 2;                    // 0 -> 1 -> 0 once every P seconds
  const trav = (t, Lx = L) => t / Lx - Math.sin(TAU * t / Lx) / TAU;        // one full step per loop, pausing at the seam
  const ease = (x) => x <= 0 ? 0 : x >= 1 ? 1 : (1 - Math.cos(Math.PI * x)) / 2;
  // 0 before a, eases up to 1 over r1 seconds, holds, eases back down over r2 seconds ending at b, 0 after
  const env = (t, a, b, r1, r2 = r1) => t <= a || t >= b ? 0 : t < a + r1 ? ease((t - a) / r1) : t > b - r2 ? ease((b - t) / r2) : 1;
  const DT = 0.25;   // one keyframe every quarter second; the browser blends smoothly in between

  /* Turn fn(t) -> {tx, ty, rot, skew, sx, sy, op, post} into Web Animation keyframes for one loop. Every keyframe has the
     same transform shape, so the canvas drawing path in bg-anim.js can draw it. When wrapKey(t) changes (something
     jumping back to its start while invisible) two keyframes are put at the exact same moment so it jumps there
     instantly instead of sliding back across the picture. */
  function build(fn, dur = L, dt = DT, wrapKey) {
    const kfs = [], N = Math.round(dur / dt);
    const toKF = (t, off) => {
      const v = fn(t), k = { offset: off };
      if (v.tx !== undefined || v.ty !== undefined || v.rot !== undefined || v.skew !== undefined || v.sx !== undefined || v.sy !== undefined)
        k.transform = `translate(${(v.tx || 0).toFixed(3)}px,${(v.ty || 0).toFixed(3)}px) rotate(${(v.rot || 0).toFixed(4)}deg) skewX(${(v.skew || 0).toFixed(5)}deg) scale(${(v.sx ?? 1).toFixed(5)},${(v.sy ?? 1).toFixed(5)})` + (v.post ? ' ' + v.post : '');
      if (v.op !== undefined) k.opacity = Math.max(0, Math.min(1, v.op)).toFixed(4);
      return k;
    };
    let prev = wrapKey ? wrapKey(0) : 0;
    for (let i = 0; i <= N; i++) {
      const t = i * dt;
      if (wrapKey && i > 0) {
        const kk = wrapKey(t);
        if (kk !== prev) {   // find the exact moment of the jump (bisection) and add the matching pair of keyframes
          let lo = t - dt, hi = t;
          for (let j = 0; j < 30; j++) { const m = (lo + hi) / 2; if (wrapKey(m) === prev) lo = m; else hi = m; }
          kfs.push(toKF(lo, lo / dur)); kfs.push(toKF(hi, lo / dur)); prev = kk;
        }
      }
      kfs.push(toKF(t, t / dur));
    }
    kfs[0].offset = 0; kfs[kfs.length - 1].offset = 1;
    for (let i = 1; i < kfs.length; i++) if (kfs[i].offset < kfs[i - 1].offset) kfs[i].offset = kfs[i - 1].offset;
    return kfs;
  }

  // One parts.js motion list -> fn(t). Several motions on one picture add up.
  function specFn(m) {
    const f = [];
    for (const c of [].concat(m)) {
      if (c.type === 'sway') f.push((t, v) => { v.skew = (v.skew || 0) + (c.dir || 1) * Math.atan(B.sway(t, c.amp, c.lag || 0)) * DEG; });
      else if (c.type === 'drift') f.push((t, v) => { const k = c.prop || 'tx', b0 = (k === 'sx' || k === 'sy') ? 1 : 0; v[k] = (v[k] ?? b0) + c.amp * osc(t, c.period || L); });
      else if (c.type === 'breathe') f.push((t, v) => { v.op = (v.op ?? (c.base || 0)) + c.amp * osc(t, c.period || L); });
      else if (c.type === 'travel') f.push((t, v) => { v[c.prop || 'tx'] = (v[c.prop || 'tx'] || 0) + (c.from || 0) + c.dist * trav(t); });
      else if (c.type === 'loop') f.push((t, v) => {   // closed ellipse that starts and ends at rest
        const s = t / c.period, th = TAU * (s - Math.sin(TAU * s) / TAU), ca = Math.cos(c.ang || 0), sa = Math.sin(c.ang || 0);
        const a = c.r * Math.sin(th), b = c.r * (c.ecc ?? 1) * (1 - Math.cos(th));
        v.tx = (v.tx || 0) + a * ca - b * sa; v.ty = (v.ty || 0) + a * sa + b * ca; });
    }
    return (t) => { const v = {}; f.forEach((g) => g(t, v)); return v; };
  }

  /* Dust motes (Linen): tiny soft specks drifting up and sideways on closed paths, brightest near the light
     (bandX(t, yFrac) gives the light's centre at that height as a fraction of the width); invisible at t = 0 and t = L. */
  function addMotes(def, c, list, bandX, src) {
    const W = c.j.W, H = c.j.H;
    for (const m of list) {
      const s = m.s;
      def.parts.push({ src, x: m.x * W - s / 2, y: m.y * H - s / 2, w: s, h: s, initial: { opacity: 0 }, keyframes: () => build((t) => {
        const q = t / m.per, th = TAU * (q - Math.sin(TAU * q) / TAU);
        const dx = m.ph * m.rx * W * Math.sin(th), dy = -m.ry * H * (1 - Math.cos(th)) / 2;
        const bx = bandX ? bandX(t, m.y) : null, prox = bx === null ? 1 : Math.exp(-Math.pow((m.x - bx) / 0.22, 2));
        return { tx: dx, ty: dy, op: m.a * osc(t, L) * (0.25 + 0.75 * prox) };
      }) });
    }
  }

  /* Load <base><scheme>-<size>/parts.js and turn it into a scene for BgAnim.Scene. extra(def, ctx) adds the scene's own
     moving bits (fish, needle, constellations). swayScale in parts.js is the per-scene sway strength Luke approved. */
  async function load(scheme, size, base, extra) {
    const dir = `${base}${scheme}-${size}/`;
    const j = await (await fetch(dir + 'parts.js?v=1')).json();
    const def = { W: j.W, H: j.H, still: dir + j.still, parts: [] };
    const ss = +(j.swayScale ?? 1);
    const scaled = (m) => [].concat(m).map((c) => c.type === 'sway' && ss !== 1 ? Object.assign({}, c, { amp: c.amp * ss }) : c);
    for (const p of j.parts) {
      const fn = p.motion ? specFn(scaled(p.motion)) : null;
      def.parts.push({ src: dir + p.src, x: p.x || 0, y: p.y || 0, w: p.w, h: p.h, origin: p.origin, role: p.role,
        initial: p.motion && p.motion.some((c) => c.type === 'breathe') && !p.motion.some((c) => c.base) ? { opacity: 0 } : undefined,
        keyframes: fn ? () => build(fn) : () => null });
    }
    if (extra) await extra(def, { scheme, size, j, dir, url: (n) => dir + n });
    return def;
  }

  window.BgMotion = { osc, trav, ease, env, build, specFn, addMotes, DT, load };
  window.BgScenes = window.BgScenes || {};
})();
