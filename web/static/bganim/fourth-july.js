/* Fourth of July scene for the animated backgrounds (BgAnim), added Oct 19 2026 (26.10.4 fix).
   Before this, the Fourth of July background had no moving version at all, so turning on "Animated backgrounds" did
   nothing while it was picked. While it moves now:
     - small fireworks burst in the open sky every few seconds (red, white, blue and gold sparks spread out, droop a
       little and fade),
     - the sparklers along the shore flicker,
     - the stars twinkle softly in three groups.
   The flag, lake, mountains and trees stay still.
   The painting is the unchanged seasonal picture (static/bg/seasonal/fourth-july-*.webp), and every moving piece is
   invisible at the start and end of the 60 s loop, so the still picture IS the first and last frame: no jump when the
   animation starts or stops. Files: bganim/fourth-july/<scheme>-<size>/parts.js (plain JSON) and the small pictures
   next to it, made by /workspace/july4-bg-fix/gen_july4.py. Nothing here changes the shared runtime (bg-anim.js). */
(function () {
  const TAU = Math.PI * 2;
  const L = 60;      // loop length in seconds (same as BgAnim.L)
  const DT = 0.2;    // one keyframe every fifth of a second; the browser blends smoothly in between
  const SEASON_V = 1; // must match the ?v= the app uses for the Fourth of July pictures (seasonUrl in app.js)

  const ease = (x) => x <= 0 ? 0 : x >= 1 ? 1 : (1 - Math.cos(Math.PI * x)) / 2;
  // 0 at the very start and end of the loop, rising to 1 over the first and last r seconds
  const edge = (t, r) => ease(Math.min(t, L - t) / r);
  // a repeatable random number generator, so every device draws the same fireworks
  let seed = 704;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };

  // Turn fn(t) -> {tx, ty, op} into Web Animation keyframes for one 60 s loop (translate + opacity only, like Ember,
  // so the canvas painter in bg-anim.js can draw it).
  function build(fn) {
    const kfs = [], N = Math.round(L / DT);
    for (let i = 0; i <= N; i++) {
      const t = i * DT, v = fn(t), k = { offset: t / L };
      if (v.tx !== undefined || v.ty !== undefined) k.transform = `translate(${(v.tx || 0).toFixed(2)}px,${(v.ty || 0).toFixed(2)}px)`;
      k.opacity = Math.max(0, Math.min(1, v.op)).toFixed(4);
      kfs.push(k);
    }
    kfs[0].offset = 0; kfs[kfs.length - 1].offset = 1;
    return kfs;
  }

  async function load(scheme, size, base) {
    base = base || new URL('fourth-july/', document.currentScript ? document.currentScript.src : location.href).href;
    const dir = `${base}${scheme}-${size}/`;
    const j = await (await fetch(dir + 'parts.js?v=1')).json();
    const W = j.W, H = j.H;
    const painting = `/static/bg/seasonal/${j.still}?v=${SEASON_V}`;
    seed = 704;
    const parts = [{ src: painting, x: 0, y: 0, w: W, h: H, keyframes: () => null }];   // the still painting underneath

    for (const p of j.parts) {
      const part = { src: dir + p.src, x: p.x, y: p.y, w: p.w, h: p.h, initial: { opacity: 0 } };
      if (p.role === 'sparkle') {
        // quick, uneven flicker made of three rhythms that all repeat a whole number of times per loop
        part.keyframes = () => build((t) => ({ op: edge(t, 2) * (0.35 + 0.25 * Math.sin(TAU * t * 1.3) + 0.2 * Math.sin(TAU * t * 2.9 + 1) + 0.15 * Math.sin(TAU * t * 0.7 + 2)) }));
      } else if (p.role === 'stars') {
        const g = p.group, P = [6, 7.5, 10][g];   // each group twinkles on its own period (all divide 60)
        part.keyframes = () => build((t) => ({ op: edge(t, 3) * 0.55 * (1 - Math.cos(TAU * t / P + g * 2)) / 2 }));
      }
      parts.push(part);
    }

    // Fireworks: one burst roughly every 5 s, none in the first or last 2 s so the loop starts and ends on the still.
    const [zx0, zy0, zx1, zy1] = j.burst_zone, S = j.dot_px * 1.8, R = j.burst_r;   // sparks a bit bigger than the generator default so they read on a phone
    const colours = ['red', 'white', 'blue', 'gold'];
    const SPARKS = 28, LIFE = 2.8;
    for (let b = 0; b < 11; b++) {
      const t0 = 2.5 + b * 5 + rnd() * 1.5;
      const cx = (zx0 + (zx1 - zx0) * rnd()) * W, cy = (zy0 + (zy1 - zy0) * rnd()) * H;
      const col = colours[b % colours.length], col2 = colours[(b + 1 + Math.floor(rnd() * 3)) % 4];
      const r = R * (0.7 + 0.5 * rnd());
      for (let s = 0; s < SPARKS; s++) {
        const ang = TAU * s / SPARKS + rnd() * 0.25, reach = r * (0.75 + 0.35 * rnd());
        const sz = S * (0.8 + 0.5 * rnd());
        parts.push({ src: `${dir}dot-${s % 3 ? col : col2}.webp`, x: cx - sz / 2, y: cy - sz / 2, w: sz, h: sz, initial: { opacity: 0 },
          keyframes: () => build((t) => {
            const u = (t - t0) / LIFE;
            if (u <= 0 || u >= 1) return { tx: 0, ty: 0, op: 0 };
            const out = 1 - Math.pow(1 - u, 3);                 // shoots out fast, then slows
            return { tx: Math.cos(ang) * reach * out, ty: Math.sin(ang) * reach * out + R * 0.25 * u * u,   // and droops
                     op: Math.min(1, u * 12) * Math.pow(1 - u, 1.4) };
          }) });
      }
    }
    return { id: 'fourth-july', W, H, still: painting, parts, alignY: 0.5 };   // centred like the app shows the holiday stills
  }
  window.BgScenes = window.BgScenes || {}; window.BgScenes['fourth-july'] = { load };
})();
