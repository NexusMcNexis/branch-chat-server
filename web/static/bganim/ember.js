/* Ember scene for the animated backgrounds (BgAnim), added Oct 19 2026 (26.10.3).
   The picture is the approved Ember campfire painting. While it moves: small embers rise from the campfire, drift and
   sway a little and fade out; the warm glow around the fire slowly breathes. The tent, trees and sky stay still.
   Files: bganim/ember/<scheme>-<size>/parts.js (plain JSON, named .js because the app server only serves known file
   types) plus the pictures next to it. Made by mockups/animated-backgrounds/ember/gen_ember.py (phone) and
   gen_ember_desktop.py (desktop).
   Oct 19 2026 (26.10.4): denser, 105 embers instead of 35 (Luke wanted it closer to the earlier fuller preview); made by
   mockups/animated-backgrounds/ember/gen_ember_dense.py. The still was remade with the new frame-0 embers.
   Like every scene, the loop is 60 s and starts and ends exactly on the still picture: at t = 0 the embers you see are
   baked into the still; over the first 3 s a copy of the plain painting fades over those baked embers while the moving
   embers fade in on the very same spots, and the last 3 s do the reverse. Nothing here changes the shared runtime. */
(function () {
  const TAU = Math.PI * 2;
  const L = 60;      // loop length in seconds (same as BgAnim.L)
  const DT = 0.25;   // one keyframe every quarter second; the browser blends smoothly in between

  // Small motion helpers. All of them are at rest (value 0, not moving) at t = 0 and t = 60, so the loop is seamless.
  const osc = (t, P) => (1 - Math.cos(TAU * t / P)) / 2;               // 0 -> 1 -> 0 once every P seconds
  const trav = (t) => t / L - Math.sin(TAU * t / L) / TAU;             // travels one full step per loop, pausing at the seam
  const ease = (x) => x <= 0 ? 0 : x >= 1 ? 1 : (1 - Math.cos(Math.PI * x)) / 2;
  // 0 before a, eases up to 1 over r1 seconds, holds, eases back to 0 over r2 seconds ending at b
  const env = (t, a, b, r1, r2) => t <= a || t >= b ? 0 : t < a + r1 ? ease((t - a) / r1) : t > b - r2 ? ease((b - t) / r2) : 1;

  /* Turn fn(t) -> {tx, ty, op} into Web Animation keyframes for one 60 s loop. When wrapKey(t) changes (an ember
     reaching the top and starting again at the fire) two keyframes are put at the exact same moment so it jumps
     there instantly while invisible, instead of sliding back down through the picture. Every keyframe uses the same
     transform shape (translate only) so the canvas drawing path in bg-anim.js can draw it. */
  function build(fn, wrapKey) {
    const kfs = [], N = Math.round(L / DT);
    const toKF = (t, off) => {
      const v = fn(t), k = { offset: off };
      if (v.tx !== undefined || v.ty !== undefined) k.transform = `translate(${(v.tx || 0).toFixed(3)}px,${(v.ty || 0).toFixed(3)}px)`;
      if (v.op !== undefined) k.opacity = Math.max(0, Math.min(1, v.op)).toFixed(4);
      return k;
    };
    let prev = wrapKey ? wrapKey(0) : 0;
    for (let i = 0; i <= N; i++) {
      const t = i * DT;
      if (wrapKey && i > 0) {
        const kk = wrapKey(t);
        if (kk !== prev) {   // find the exact moment of the jump (bisection) and add the matching pair of keyframes
          let lo = t - DT, hi = t;
          for (let j = 0; j < 30; j++) { const m = (lo + hi) / 2; if (wrapKey(m) === prev) lo = m; else hi = m; }
          kfs.push(toKF(lo, lo / L)); kfs.push(toKF(hi, lo / L)); prev = kk;
        }
      }
      kfs.push(toKF(t, t / L));
    }
    kfs[0].offset = 0; kfs[kfs.length - 1].offset = 1;
    for (let i = 1; i < kfs.length; i++) if (kfs[i].offset < kfs[i - 1].offset) kfs[i].offset = kfs[i - 1].offset;
    return kfs;
  }

  async function load(scheme, size, base) {
    base = base || new URL('ember/', document.currentScript ? document.currentScript.src : location.href).href;
    const dir = `${base}${scheme}-${size}/`;
    const j = await (await fetch(dir + 'parts.js?v=2')).json();
    const W = j.W, H = j.H, [FX, FY] = j.fire, R = j.ramp_s || 3;
    const fade = (t) => env(t, 0, L, R, R);   // 0 at the start and end of the loop, 1 in between
    const parts = [];
    // the painting, the "plain painting" patch over the baked embers, and the breathing campfire glow
    for (const p of j.parts) {
      const part = { src: dir + p.src, x: p.x || 0, y: p.y || 0, w: p.w, h: p.h, keyframes: () => null };
      if (p.role === 'patch') part.keyframes = () => build((t) => ({ op: fade(t) }));
      else if (p.motion && p.motion[0] && p.motion[0].type === 'breathe') {
        const m = p.motion[0];
        part.initial = { opacity: 0 };
        part.keyframes = () => build((t) => ({ op: m.amp * osc(t, m.period || L) }));
      }
      parts.push(part);
    }
    // the rising embers: each one climbs from the fire, drifts sideways, sways, flickers and fades near the top
    const dot = dir + 'ember-dot.webp';
    for (const e of j.embers) {
      const s = e.size, U = (t) => e.u0 + trav(t);
      parts.push({ src: dot, x: FX * W - s / 2, y: FY * H - s / 2, w: s, h: s, initial: { opacity: 0 },
        keyframes: () => build((t) => {
          const u = U(t) % 1;
          return { tx: e.x0 + e.xs * u + e.sw * osc(t, e.sp), ty: -u * e.D,
                   op: fade(t) * e.amax * Math.pow(Math.sin(Math.PI * u), 0.7) * (1 - 0.35 * osc(t, e.fp)) };
        }, (t) => Math.floor(U(t))) });
    }
    return { id: 'ember', W, H, still: dir + j.still, parts };
  }
  window.BgScenes = window.BgScenes || {}; window.BgScenes.ember = { load };
})();
