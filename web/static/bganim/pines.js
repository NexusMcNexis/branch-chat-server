/* Pines scene for the animated backgrounds (BgAnim). Parts come from mockups/animated-backgrounds/src/gen_pines_layers.py:
   bganim/pines/<scheme>-<size>/parts.js (JSON) plus the still pines-<scheme>-<size>.webp (frame 0 of the loop). */
(function () {
  const TAU = Math.PI * 2;
  function rng(seed) { return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }; }
  async function load(scheme, size, base) {
    base = base || new URL('assets/', document.currentScript ? document.currentScript.src : location.href).href;
    const dir = `${base}${scheme}-${size}/`;
    // the parts list is plain JSON; the file is named .js because the app server only serves known file types
    const m = await (await fetch(dir + 'parts.js?v=1')).json();
    const { sway, sample, breathe = BgAnim.shapes.breathe } = BgAnim;
    const lagSpan = size === 'phone' ? 3.0 : 3.5;
    const thMax = {}; m.sprites.forEach(s => thMax[s.layer] = Math.max(thMax[s.layer] || 0, s.th));
    const parts = [];
    parts.push({ src: dir + m.patch.f, x: m.patch.x, y: m.patch.y, w: m.patch.w, h: m.patch.h, keyframes: () => null });
    // overlays that sit behind the trees (mist, stars, halo): opacity 0 at t=0, so frame 0 is the still
    for (const o of m.overlays) {
      let kf;
      if (o.kind === 'mist') {
        const D = 0.005 * m.W;
        kf = () => sample(t => ({ opacity: +(0.10 * BgAnim.shapes.breathe(t, 20)).toFixed(4),
                                  transform: `translateX(${(D * BgAnim.shapes.breathe(t, 60)).toFixed(2)}px)` }));
      } else if (o.kind === 'stars') {
        const P = [12, 15, 20][o.g], dip = [0.10, 0.125, 0.15][o.g];
        kf = () => sample(t => ({ opacity: +(dip * BgAnim.shapes.breathe(t, P)).toFixed(4) }));
      } else if (o.kind === 'halo') {
        kf = () => sample(t => ({ opacity: +BgAnim.shapes.breathe(t, 30).toFixed(4) }));
      }
      parts.push({ src: dir + o.f, x: o.x, y: o.y, w: o.w, h: o.h, initial: { opacity: 0 }, keyframes: kf, z: o.kind });
    }
    // trees, in exact draw order (back to front). STAGGER RULE (Oct 18 2:13 AM PT): each tree has its own reaction
    // delay, response speed, amplitude, resonant period and flutter (BgAnim.sway variation, seeded per tree); the gust
    // front still travels left to right.
    // v3 (Oct 18 6:55 PM PT: "too slow and not far enough"): the wind runs twice as fast (4 shorter gusts per loop
    // instead of 2, and every sway / flutter / swell period halved) and every tree leans twice as far as before.
    // The four gusts have different strengths (shared by all trees and the drifting leaves) so it never feels mechanical.
    const legacy = BgAnim.legacySway;
    const v3 = !legacy && !!BgAnim.gustEnv;                                    // runtime supports tempo mode
    const TEMPO = v3 ? 2 : 1, GUSTS = [1, 0.7, 0.9, 0.62];                     // strength of the 4 gusts (0-15, 15-30, 30-45, 45-60 s)
    const AMP = v3 ? 3 : 1;                                                    // v4 (7:46 PM): 3x the 2:13 AM amplitudes (v3's 2x plus another 50 %)
    const lastBack = m.sprites.reduce((k, s, i) => (s.layer === 5 ? i : k), -1);   // last tree of the far slope (for particle depth)
    m.sprites.forEach((s, i) => {
      const r = rng(7919 * (i + 1) + (size === 'phone' ? 177 : 77));
      const fg = s.layer === 6 && s.th > 300;
      const nominal = legacy ? (s.layer === 5 ? 0.006 : (fg ? 0.009 : 0.006)) : AMP * (s.layer === 5 ? 0.013 : (fg ? 0.019 : 0.013));
      const A = nominal * (0.75 + 0.5 * r());                                   // ±25 % seeded jitter (on top of the per-tree amp variation)
      const inertia = (legacy ? 0.5 : 0.3) * (s.th / thMax[s.layer]) * (0.6 + 0.4 * r());
      const lam = Math.min(1, Math.max(0, s.ox / m.W)) * lagSpan + inertia;
      const seed = 104729 * (i + 1) + (scheme === 'dark' ? 31 : 17) + (size === 'phone' ? 5 : 3);
      const vars = BgAnim.swayVars ? BgAnim.swayVars(seed, v3 ? { tempo: TEMPO, gusts: GUSTS } : undefined) : undefined;
      // at 2x tempo the fastest flutter is ~0.75 s, so sample 16 times a second to keep the motion round, not angular
      parts.push({ src: dir + s.f, x: s.x, y: s.y, w: s.w, h: s.h, origin: `${(s.ox - s.x).toFixed(1)}px ${(s.oy - s.y).toFixed(1)}px`,
        keyframes: () => sample(t => ({ transform: `skewX(${(-Math.atan(sway(t, A, lam, vars)) * 180 / Math.PI).toFixed(4)}deg)` }), legacy ? BgAnim.DT : (v3 ? 0.0625 : 0.125)),
        meta: { A, lam, vars, layer: s.layer, th: s.th }, back: i <= lastBack });
    });
    // overlays first (behind trees), keep patch at the very bottom
    const patch = parts.shift(); const ovs = parts.filter(p => p.z); const trees = parts.filter(p => !p.z);
    const baseStill = { src: base + m.still, x: 0, y: 0, w: m.W, h: m.H, keyframes: () => null };   // the approved still is the bottom of the stack
    // v3 breeze: far particles fly between the far slope and the foreground trees, near ones in front of everything
    const fx = v3 && !BgAnim.noBreeze ? breeze(scheme, size, m.W, m.H, base + 'fx/', GUSTS) : [];
    const backTrees = trees.filter(p => p.back), frontTrees = trees.filter(p => !p.back);
    // v4 extras: sky (clouds, warm skyline) and far hills (deer, moose) sit behind every tree; the campfire glow, its smoke
    // and the fireflies sit between the far slope and the foreground trees, so they show through the gaps between trunks
    const ex = v3 && !BgAnim.noExtras ? extras(scheme, size, m, base + 'fx4/', GUSTS) : { sky: [], hills: [], mid: [], near: [] };
    return { id: 'pines', W: m.W, H: m.H, still: base + m.still,
             parts: [baseStill, patch, ...ovs, ...ex.sky, ...ex.hills, ...backTrees, ...ex.mid, ...fx.filter(p => p.far), ...frontTrees, ...ex.near, ...fx.filter(p => !p.far)], manifest: m };
  }

  /* ---- BREEZE (Pines v3, Oct 18 2026 6:55 PM PT): things floating by with the wind ----
     A few small sprites (dust wisps, pollen motes, pine needles, a winged pine seed, small leaves) drift left to right,
     the same way the gusts travel, speeding up as each gust passes. Each sprite is one <img> moved only with transform
     and opacity (Web Animations, on the same 60 s clock as the trees), so the browser can run it off the main thread:
     no canvas redraws, no layout. A sprite can make one trip in several of the four gust windows; between trips it is
     invisible and simply jumps back. Every trip fades in and out and lies inside 0.6-59.4 s, so at t = 0 and t = 60
     nothing is visible and the frame is exactly the still (the loop stays seamless).
     Sized to still read through the frosted (blurred) chat panels, but kept gentle: mostly faint, few at a time (phone 12 sprites, desktop 22), varied size, speed and depth. */
  function breeze(scheme, size, W, H, dir, GUSTS) {
    const { gustEnv, rngOf } = BgAnim;
    const phone = size === 'phone';
    const k = phone ? 0.302 : 0.75;                  // stage px per CSS px at the reference screen (390 px phone, 1920 px desktop)
    const css = (v) => v / k;                        // sizes and speeds below are in CSS px at that reference screen
    const r = rngOf(phone ? 4242 : 2424);
    const pick = (a) => a[Math.floor(r() * a.length)], lerp = (a, b) => a + (b - a) * r();
    // kind: [sprite files, length CSS px [min,max], peak opacity [min,max], how it moves]
    const KINDS = {
      wisp:   { f: ['wisp0', 'wisp1'], len: [170, 320], op: [0.38, 0.55], aspect: 48 / 256 },
      mote:   { f: ['mote0'], len: [7, 11], op: [0.7, 0.9], aspect: 1 },
      needle: { f: ['needle0', 'needle1'], len: [28, 40], op: [0.8, 0.95], aspect: 28 / 96 },
      seed:   { f: ['seed0'], len: [24, 32], op: [0.85, 0.95], aspect: 28 / 72 },
      leaf:   { f: ['leaf0', 'leaf1', 'leaf2'], len: [20, 30], op: [0.85, 0.95], aspect: 40 / 64 },
    };
    const MIX = phone ? ['wisp', 'wisp', 'wisp', 'mote', 'mote', 'needle', 'needle', 'needle', 'seed', 'leaf', 'leaf', 'leaf']
                      : ['wisp', 'wisp', 'wisp', 'wisp', 'wisp', 'mote', 'mote', 'mote', 'mote', 'needle', 'needle', 'needle', 'needle', 'needle', 'needle', 'seed', 'seed', 'leaf', 'leaf', 'leaf', 'leaf', 'leaf'];
    const vPeak = phone ? 75 : 260;                 // CSS px/s of a near sprite at the height of the strongest gust
    const dim = scheme === 'dark' ? 0.8 : 1;         // a touch fainter at night
    const DT = 0.0625, n = Math.round(60 / DT);
    const out = [];
    MIX.forEach((kind, idx) => {
      const K = KINDS[kind];
      const depth = pick([0.55, 0.75, 1.0]);         // far sprites: smaller, slower, fainter
      const far = depth < 0.6 && kind !== 'wisp';
      const len = css(lerp(K.len[0], K.len[1]) * (0.6 + 0.4 * depth)), w = len, h = len * K.aspect;
      const op = lerp(K.op[0], K.op[1]) * (0.65 + 0.35 * depth) * dim;
      // which gust windows this sprite rides: 2 or 3 of the 4, favouring the strong ones
      const wins = [0, 1, 2, 3].filter(wi => r() < 0.35 + 0.45 * GUSTS[wi]).slice(0, 3);
      if (wins.length < 2) wins.splice(0, wins.length, ...(idx % 2 ? [0, 2] : [1, 2]));
      const trips = wins.map(wi => {
        const ws = 15 * wi, t0 = ws + lerp(0.8, 4.5), dur = lerp(4.5, 8);
        return { wi, t0: Math.max(0.6, t0), t1: Math.min(59.4, t0 + dur, ws + 15.4), x0: lerp(-0.12, 0.6) * W, y0: lerp(0.06, phone ? 0.86 : 0.9) * H,
                 fall: css(kind === 'wisp' || kind === 'mote' ? lerp(-4, 6) : lerp(8, 22)), bobA: css(lerp(4, 14)), bobP: lerp(1.6, 3.2), bobPh: r() * 6.283,
                 a0: lerp(-30, 30), spin: (r() < 0.5 ? -1 : 1) * (kind === 'seed' ? lerp(220, 360) : kind === 'needle' ? lerp(60, 200) : lerp(40, 140)),
                 flipP: lerp(0.8, 1.6), flipPh: r() * 6.283, gd: lerp(0.3, 1.4) };
      });
      const kf = [];
      const hidden = (t) => kf.push({ offset: t / 60, opacity: 0, transform: `translate(${(-w * 2).toFixed(1)}px, ${(-h * 2).toFixed(1)}px) rotate(0deg) scale(1, 1)` });
      hidden(0);
      for (const tr of trips) {
        // integrate the motion through the trip: speed follows this window's gust (plus a light steady breeze)
        let x = tr.x0, y = tr.y0, rot = tr.a0, flip = tr.flipPh, prevE = 0;
        const i0 = Math.ceil(tr.t0 / DT), i1 = Math.floor(tr.t1 / DT);
        hidden(Math.max(0, i0 * DT - DT));
        for (let i = i0; i <= i1; i++) {
          const t = i * DT;
          const E = gustEnv(t - 15 * tr.wi - tr.gd, 9.5) * GUSTS[tr.wi];       // 0..1: how hard the wind is blowing here now
          const sp = 0.22 + 0.78 * E;
          x += css(vPeak) * depth * sp * DT;
          y += (tr.fall * (1 - 0.6 * E) - css(18) * depth * (E - prevE) / DT * 0.6) * DT;   // falls slowly, lifts a little as a gust builds
          prevE = E;
          rot += tr.spin * (0.35 + E) * DT; flip += (6.283 / tr.flipP) * (0.4 + E) * DT;
          const fadeIn = Math.min(1, (t - tr.t0) / 0.9), fadeOut = Math.min(1, (tr.t1 - t) / 1.1);
          const ease = (q) => q * q * (3 - 2 * q);
          let o = op * ease(Math.max(0, Math.min(fadeIn, fadeOut)));
          if (x > W + w || x < -w * 1.5) o = 0;                                // off screen: nothing to draw
          const yy = y + tr.bobA * Math.sin(6.283 * t / tr.bobP + tr.bobPh);
          let a, sx = 1, sy = 1;
          if (kind === 'wisp') { a = -3 + 6 * Math.sin(t / 2.3 + tr.bobPh); sx = 0.8 + 0.5 * E; sy = 1 - 0.2 * E; o *= 0.8 + 0.2 * Math.sin(6.283 * t / 2.7 + tr.flipPh); }
          else if (kind === 'mote') { a = 0; }
          else if (kind === 'leaf') { a = rot; sy = Math.cos(flip); }          // tumbling: the leaf turns edge-on and over
          else if (kind === 'seed') { a = rot; sy = 0.55 + 0.45 * Math.cos(flip); }
          else { a = rot; sy = 0.8 + 0.2 * Math.cos(flip); }
          kf.push({ offset: t / 60, opacity: +Math.max(0, o).toFixed(3),
                    transform: `translate(${x.toFixed(1)}px, ${yy.toFixed(1)}px) rotate(${a.toFixed(2)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})` });
        }
        hidden(Math.min(60, i1 * DT + DT));
      }
      hidden(60);
      // drop repeated invisible keyframes (keeps memory small; nothing visible changes)
      const kept = kf.filter((q, i) => i === 0 || i === kf.length - 1 || q.opacity > 0 || kf[i - 1].opacity > 0 || kf[i + 1].opacity > 0);
      const f = pick(K.f);
      out.push({ src: `${dir}${scheme}-${f}.webp`, x: 0, y: 0, w: +w.toFixed(1), h: +h.toFixed(1), origin: '50% 50%',
                 initial: { opacity: 0 }, keyframes: () => kept, far, z: null, fx: kind });
    });
    return out;
  }

  /* ---- v4 EXTRAS (Oct 18 2026 7:46 / 7:47 PM PT) ----
     Light: wispy clouds drifting and slowly changing form, a warmer yellow-orange skyline, a soaring hawk and small birds.
     Dark (v4b, 8:07-8:09 PM PT): a small far-off campfire in an open clearing (a little flickering flame, a wide soft
     glow and light cones that nearer trunks cut into shafts, smoke rising high and only bending downwind above mid-screen)
     and many fireflies at three depths that glow for a few seconds, drift, fade, and light up again somewhere else.
     Both: tiny far-off deer and moose that walk across the distant hills now and then.
     Same rules as everything else: one <img> per piece, transform + opacity only, no filters; every piece is invisible
     at t = 0 and t = 60 s, so the loop starts and ends on the still picture and the seam stays exact. */
  function extras(scheme, size, m, dir, GUSTS) {
    const { gustEnv, rngOf } = BgAnim;
    const phone = size === 'phone', W = m.W, H = m.H, dark = scheme === 'dark';
    const k = phone ? 0.302 : 0.75, css = (v) => v / k;            // CSS px at the reference screen -> stage px
    const r = rngOf(phone ? 777 : 555), lerp = (a, b) => a + (b - a) * r();
    const DT = 0.0625, N = Math.round(60 / DT);
    const ease = (q) => { q = Math.max(0, Math.min(1, q)); return q * q * (3 - 2 * q); };
    const plateau = (t, a, b, ramp) => ease((t - a) / ramp) * ease((b - t) / ramp);   // 0 outside [a,b], soft ramps
    const wind = (t) => { let e = 0; for (let w = 0; w < 4; w++) e += gustEnv(t - 15 * w - 1, 9.5) * GUSTS[w]; return e; };
    const tf = (x, y, rot, sx, sy) => `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${rot.toFixed(2)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
    // build keyframes from fn(t) -> {o, x, y, rot, sx, sy}; repeated invisible frames are dropped to keep memory small
    const track = (fn) => { const kf = []; for (let i = 0; i <= N; i++) { const t = i * DT, q = fn(t);
        const o = (i === 0 || i === N) ? 0 : Math.max(0, q.o);
        kf.push({ offset: i / N, opacity: +o.toFixed(3), transform: tf(q.x, q.y, q.rot || 0, q.sx || 1, q.sy || 1) }); }
      return kf.filter((q, i) => i === 0 || i === kf.length - 1 || q.opacity > 0 || kf[i - 1].opacity > 0 || kf[i + 1].opacity > 0); };
    const part = (f, w, h, fn, origin) => { const kf = track(fn); return { src: dir + f, x: 0, y: 0, w: +w.toFixed(1), h: +h.toFixed(1), origin: origin || '50% 50%', initial: { opacity: 0 }, keyframes: () => kf, z: null, fx: f }; };
    // where things are, read from the scene itself: the far-slope trees (layer 5) give the forest floor and the tree line
    const l5 = m.sprites.filter(s => s.layer === 5);
    const treeTop = Math.min(...l5.map(s => s.y)) || H * 0.7;                       // highest far tree top
    const skyline = phone ? 0.37 * H : 0.40 * H;
    const sky = [], hills = [], mid = [];
    if (!dark) {
      // warm skyline: fades in over the first 6 s, stays, fades out over the last 6 s
      const bw = W * 1.1, bh = css(phone ? 90 : 110);
      sky.push(part('warm.webp', bw, bh, (t) => ({ o: 0.55 * plateau(t, 0, 60, 6), x: -W * 0.05, y: skyline - bh * 0.55 })));
      // clouds (7:49 PM: wispy, airy, slowly changing form): each cloud is 3 thin, soft streak sprites stacked on the
      // same drifting path; they cross-fade and stretch at different slow rates, so the cloud seems to reshape as it floats
      const nc = phone ? 3 : 4;
      for (let i = 0; i < nc; i++) {
        const cw = css(phone ? lerp(170, 250) : lerp(300, 460)), ch = cw / 4;
        const x0 = W * (i / nc) - cw * 0.3 + lerp(-0.05, 0.05) * W, drift = W * lerp(0.12, 0.2), y0 = lerp(0.04, phone ? 0.22 : 0.26) * H, op = lerp(0.5, 0.75);
        for (let j = 0; j < 3; j++) {
          const P = [20, 30, 60][j], ph = j * 2.1, dx = (j - 1) * cw * 0.12, dy = (j - 1) * ch * 0.15;   // periods divide 60: smooth loop
          sky.push(part(`wisp${(i * 3 + j) % 6}.webp`, cw, ch, (t) => {
            const c = 0.5 + 0.5 * Math.sin(6.283 * t / P + ph);              // this layer's share of the cloud right now
            return { o: op * (0.35 + 0.65 * c) * plateau(t, 0, 60, 7), x: x0 + dx + drift * (t / 60) + css(6) * wind(t), y: y0 + dy,
                     sx: 0.9 + 0.2 * c, sy: 0.85 + 0.3 * (1 - c) };
          }));
        }
      }
      // birds (7:49 PM, light only): a hawk soaring in wide slow arcs with an occasional few wingbeats, and small birds
      // flapping across now and then, at different distances. Two wing poses per bird swap by opacity.
      const bird = (kind, w, path, flapAt) => { for (const pose of [0, 1]) sky.push(part(`${kind}${pose}.webp`, w, w * (kind === 'raptor' ? 24 / 64 : 14 / 24), (t) => {
          const q = path(t); const up = flapAt(t); return { o: q.o * (up === pose ? 1 : 0), x: q.x, y: q.y, rot: q.rot || 0 }; })); };
      [[3, 30, 0.3, 0.12, 1], [32, 57, 0.7, 0.17, 0.7]].forEach(([t0, t1, cxf, cyf, sz]) => {
        const w = css((phone ? 34 : 46) * sz), R = css(phone ? 60 : 120) * sz, cx = W * cxf, cy = H * cyf, dir = cxf < 0.5 ? 1 : -1;
        bird('raptor', w, (t) => { const u = (t - t0) / (t1 - t0), a = dir * 6.283 * 0.9 * u;   // most of one big slow circle, drifting downwind
            return { o: 0.85 * plateau(t, t0, t1, 2.5), x: cx + R * 1.6 * Math.sin(a) + css(25) * u - w / 2, y: cy - R * 0.5 * Math.cos(a), rot: 8 * Math.cos(a) * dir }; },
          (t) => { const ph = ((t - t0) % 9); return (ph > 6 && ph < 7.6) ? Math.floor(ph / 0.2) % 2 : 0; });   // a few beats every 9 s, else glide
      });
      [[12, 0.30, 1], [12.6, 0.33, 0.8], [13.1, 0.28, 0.9], [46, 0.2, 0.6], [47.5, 0.24, 0.7]].forEach(([t0, yf, sz], i) => {
        const w = css((phone ? 13 : 16) * sz), dur = phone ? 9 : 14, sp = (W + w * 2) / dur, y0 = H * yf, fp = 0.16 + 0.04 * (i % 3);
        bird('small', w, (t) => { const u = t - t0; return { o: 0.8 * plateau(t, t0, Math.min(58.5, t0 + dur), 0.8), x: -w + sp * u, y: y0 + css(5) * Math.sin(u * 2.1 + i) }; },
          (t) => Math.floor((t - t0) / fp) % 3 === 0 ? 0 : 1);
      });
    }
    // tiny far animals: walk a short way along the distant hills (between the skyline and the far tree tops), then fade
    const hillY = skyline + (treeTop - skyline) * 0.72;
    [['deer', phone ? 9 : 10, 8, 26, 0.55], ['moose', phone ? 11 : 12, 36, 54, 0.32]].forEach(([kind, hc, t0, t1, fx]) => {
      const ah = css(phone ? hc * 0.42 : hc * 0.55), aw = ah * (kind === 'deer' ? 40 / 28 : 48 / 34);
      const x0 = W * (phone ? fx * 0.9 : fx), speed = css(phone ? 2.2 : 3.2), y0 = hillY + (kind === 'moose' ? ah * 0.6 : 0);
      for (const pose of [0, 1]) hills.push(part(`${scheme}-${kind}${pose}.webp`, aw, ah, (t) => {
        const walk = Math.max(0, Math.min(t1, t) - t0), step = Math.floor(walk / 0.28) % 2;        // swap leg pose every 0.28 s
        return { o: 0.9 * plateau(t, t0, t1, 2.5) * (step === pose ? 1 : 0), x: x0 + speed * walk, y: y0 - ah - Math.abs(Math.sin(walk * 11.2)) * ah * 0.04 };
      }));
    });
    // shooting stars (7:48 PM; night only per 7:49 PM): two per minute, different places and angles, ~0.8 s streak whose tail fades out
    if (dark) [[17.3, 0.18, 0.06, 18, 0.8], [43.6, 0.62, 0.12, 28, 0.7]].forEach(([t0, fx, fy, deg, dur]) => {
      const mw = css(phone ? 70 : 120), mh = mw / 16, dist = css(phone ? 170 : 360), a = deg * Math.PI / 180;
      const x0 = W * fx, y0 = H * fy;
      sky.push(part(`${scheme}-meteor.webp`, mw, mh, (t) => {
        const u = (t - t0) / dur;                                           // 0..1 through the streak
        return { o: (u <= 0 || u >= 1) ? 0 : (dark ? 0.95 : 1) * Math.sin(Math.PI * u) ** 0.6,
                 x: x0 + dist * Math.cos(a) * u, y: y0 + dist * Math.sin(a) * u, rot: deg, sx: 0.5 + 0.5 * Math.min(1, u * 3) };
      }, '100% 50%'));
    });
    const near = [];
    if (dark) {
      // v4b CAMPFIRE (8:07-8:09 PM PT): the fire sits in a small open clearing on the far grassy slope (no tree in
      // front of it or right next to it; spots picked from the tree map of each layout and kept clear of the message box).
      // It is drawn behind both tree layers, so the soft light cones it throws are cut into shafts by every nearer trunk.
      const fxp = W * (phone ? 0.3349 : 0.2344), fyp = H * (phone ? 0.7997 : 0.8646);      // flame base, stage px
      const flick = (t) => 0.78 + 0.1 * Math.sin(6.283 * t / 1.3) + 0.07 * Math.sin(6.283 * t / 0.53 + 1.1) + 0.05 * Math.sin(6.283 * t / 2.9 + 2);
      // v4c (10:23 PM PT): the light stays LOW. A flat glow on the forest floor, and soft, broad cones that spread
      // out sideways along the ground and between the nearby trunks (never up into the sky), fading out after a short reach.
      const gw = css(phone ? 200 : 380), gh = gw / 4;
      hills.push(part('glow.webp', gw, gh, (t) => ({ o: 0.3 * flick(t) * plateau(t, 0, 60, 4), x: fxp - gw / 2, y: fyp - gh * 0.62,
                                                   sx: 0.97 + 0.04 * flick(t), sy: 0.96 + 0.06 * flick(t) }), '50% 62%'));
      const rw = css(phone ? 420 : 900), rh = rw / 4;                                  // ground3.webp: fire at 50 % across, 62 % down
      hills.push(part('ground3.webp', rw, rh, (t) => ({ o: 0.42 * plateau(t, 0, 60, 4) * (0.72 + 0.28 * flick(t)), x: fxp - rw / 2, y: fyp - rh * 0.62,
                                                      sx: 0.985 + 0.03 * flick(t), sy: 0.98 + 0.04 * flick(t) }), '50% 62%'));
      // smoke (v4c): rises ~5.5x higher than v4; fairly straight until about a third of the way up the screen, then the
      // breeze bends it, more and more the higher it gets (a smooth plume curving downwind). As it rises each puff grows
      // wider and fainter, so the column spreads out and thins into the air. 20 overlapping puffs, one born every 0.75 s,
      // each lives 15 s (15 divides 60, so the column is the same at 0 s and 60 s); the whole column also fades in/out
      // over the first/last 3 s. Scale and opacity only.
      const np = 20, life = 15, pw = css(phone ? 16 : 22), rise = css(phone ? 385 : 520), bendY = H * (2 / 3);
      const fsW = css(phone ? 9 : 12), fsH = fsW * 1.25;
      const sBend = Math.max(0, (fyp - fsH - bendY) / rise) * life;                    // seconds a puff takes to reach 1/3 up the screen
      const windAt = (s) => wind(((s % 60) + 60) % 60);
      const push = css(phone ? 75 : 130);                                                // top downwind speed (CSS px/s at full bend)
      for (let i = 0; i < np; i++) hills.push(part('smoke.webp', pw, pw, (t) => {
        const tl = (((t - i * life / np) % life) + life) % life, born = t - tl, u = tl / life;
        let dx = 0;                                                                     // downwind push grows with height above the bend
        for (let s = sBend; s < tl; s += 0.25) { const g = (s - sBend) / Math.max(1, life - sBend); dx += g * g * (0.35 + windAt(born + s)) * push * 0.25; }   // 0.25 s steps
        return { o: 0.34 * plateau(t, 0, 60, 3) * ease(u / 0.06) * (1 - ease((u - 0.5) / 0.5)) * (1 - 0.35 * u),
                 x: fxp - pw / 2 + dx + css(2.5) * Math.sin(tl * 0.9 + i), y: fyp - fsH - pw / 2 - rise * u, sx: 1.4 + 6.5 * u * u + 1.5 * u, sy: 1.3 + 3.6 * u * u + 1.0 * u };
      }));
      // the flame itself: small and far off, but a real flame shape (2-4 tongues), three pre-drawn frames swapping
      // irregularly plus a little height jitter, sitting on the ground of the clearing
      for (const k of [0, 1, 2]) hills.push(part(`fire${k}.webp`, fsW, fsH, (t) => {
        const which = ((Math.floor(t / 0.15 + 0.6 * Math.sin(t * 2.3) + 0.4 * Math.sin(t * 5.1)) % 3) + 3) % 3;   // irregular swaps
        return { o: 0.97 * plateau(t, 0, 60, 4) * (which === k ? 1 : 0), x: fxp - fsW / 2, y: fyp - fsH, sx: 0.92 + 0.12 * flick(t), sy: 0.82 + 0.28 * flick(t) };
      }, '50% 100%'));
      // FIREFLIES (v4b, 8:07/8:08 PM PT): clearly visible, many lit at once (about 12 on desktop, 6-7 on phone),
      // spread over three depths: tiny, dim, slow ones far back behind the far slope trees; medium ones between the
      // two tree layers; bigger, brighter ones in front of every tree. Each sprite glows for a few seconds with a gentle
      // pulse, drifts a little, fades, and then lights up again somewhere else (while invisible it simply jumps).
      // Spots stay in the lower half of the scene and clear of the side panel on desktop.
      const DEPTH = {   // size CSS px, peak opacity, drift CSS px, drift speed, height band (fraction of H), which layer list
        far:  { n: phone ? 3 : 7, sz: phone ? 9 : 11, op: 0.6,  amp: 5,  sp: 0.55, y: phone ? [0.62, 0.75] : [0.56, 0.74], to: hills },
        mid:  { n: phone ? 4 : 7, sz: phone ? 18 : 25, op: 0.88, amp: 10, sp: 0.8, y: phone ? [0.72, 0.86] : [0.72, 0.9], to: mid },
        near: { n: phone ? 3 : 7, sz: phone ? 34 : 44, op: 1,    amp: 18, sp: 1,   y: phone ? [0.66, 0.9] : [0.6, 0.93], to: near },
      };
      const xr = phone ? [0.04, 0.96] : [0.2, 0.98];
      Object.values(DEPTH).forEach((D) => {
        for (let i = 0; i < D.n; i++) {
          const ff = css(D.sz), wins = [];
          let t = 0.8 + lerp(0, 6);                                                     // lit windows: 3.5-6.5 s on, 0.6-2.5 s off (random start spreads them)
          while (true) { const dur = lerp(3.5, 6.5); if (t + dur > 59.2) break;
            wins.push({ a: t, b: t + dur, x: W * lerp(xr[0], xr[1]), y: H * lerp(D.y[0], D.y[1]), ph: r() * 6.283, p1: lerp(2.6, 4.2) / D.sp, p2: lerp(3.2, 5) / D.sp });
            t += dur + lerp(0.6, 2.5); }
          D.to.push(part('firefly2.webp', ff, ff, (tt) => {
            const w = wins.find((q) => tt >= q.a - 0.07 && tt <= q.b + 0.07) || wins[0];
            const lit = plateau(tt, w.a, w.b, 1.1), e = tt - w.a;
            return { o: D.op * lit * (0.8 + 0.2 * Math.sin(6.283 * tt / 1.7 + w.ph)),
                     x: w.x - ff / 2 + css(D.amp) * Math.sin(6.283 * e / w.p1 + w.ph), y: w.y - ff / 2 + css(D.amp * 0.6) * Math.sin(6.283 * e / w.p2 + w.ph * 1.7) - css(D.amp * 0.3) * e / 5 };
          }));
        }
      });
    }
    return { sky, hills, mid, near };
  }
  window.BgScenes = window.BgScenes || {}; window.BgScenes.pines = { load };
})();
