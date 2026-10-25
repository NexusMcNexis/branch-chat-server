/* Branch Chat animated backgrounds runtime (shipped Oct 2026; built from mockups/animated-backgrounds/runtime).
   Loaded on demand by app.js only when Animated backgrounds is switched on and the chat's background has a moving version.
   - Every scene = one still (frame 0) + parts (sprites/overlays) driven by the Web Animations API on one shared clock.
   - Keyframes are sampled every 0.5 s from rest-shaped periodic functions (section 0 of the spec), linear in between.
   - bc-bg-anim (switch) and bc-bg-speed (0.5 / 1 / 2) live in localStorage; speed applies at loop boundaries only.
   - ON: decode parts -> mount under the still at t=0 -> 2 rAF -> start all in lockstep -> fade still out (1.5 s laminar).
   - OFF: finish the loop -> pause exactly at T -> fade still in -> remove parts. Reduced motion: static, instant cut. */
(function () {
  'use strict';
  const L = 60;                                        // loop length, seconds at 1x
  const DT = 0.5;                                      // keyframe sampling step
  const TAU = Math.PI * 2;
  const shapes = {
    // p(0)=p(L)=0 and p'(0)=p'(L)=0 for all of these
    breathe: (t, P) => (1 - Math.cos(TAU * t / P)) / 2,                      // 0..1..0, period P (divides 60)
    travel: (t) => t / L - Math.sin(TAU * t / L) / TAU,                       // 0..1 over the loop, lull at the ends
    gust: (tau) => {                                                          // 30 s gust: rise 12, damped return 14, calm 4
      tau = ((tau % 30) + 30) % 30;
      if (tau < 12) return 0.5 * (1 - Math.cos(Math.PI * tau / 12));
      if (tau < 26) return 0.5 * (1 + Math.cos(Math.PI * (tau - 12) / 14));
      return 0;
    },
  };
  /* ---- SWAY (STAGGER RULE, Oct 18 2026 2:13 AM PT): every tree / foliage element gets its own timing ----
     The gust front still travels across the scene (lag lam from x), but each element also gets, from a stable seed:
       react  0.2-1.2 s extra reaction delay      k   0.75-1.3 response speed (gust profile 20 s / k)
       amp    0.8-1.2 x amplitude                 gB  0.55-0.8 strength of the second gust
       P      3.0-5.4 s own resonant sway period, osc 0.30-0.45 of the lean, starting at a random phase of the gust
       flutter 1.5-2.3 s, 0.07-0.12 of the lean    swell 12/15/20/30 s, 0.12-0.18 (periods divide 60)
     Every term is multiplied by the gust envelope g (g=0, g'=0 at both ends) or is a rest-shaped breathe, and
     lam+react+20/k <= 29.5 s is enforced, so all elements are exactly at rest at t=0 and t=60 (frame 0 = still).
     sway(t, A, lam)            -> seed derived from (A, lam): existing callers inherit the stagger automatically, and their
                                   amplitude is multiplied by SWAY.inheritScale (2.1) because they were tuned to the old Pines
     sway(t, A, lam, seed|vars) -> explicit seed number or a variation object from swayVars(seed)
     BgAnim.legacySway = true   -> the pre-2:13 AM uniform sway (review comparisons only) */
  function hashSeed(a, b) { let h = 2166136261 >>> 0; const s = a.toFixed(6) + '|' + b.toFixed(4);
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; } return h; }
  function rngOf(seed) { let x = (seed >>> 0) || 1; return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; }; }
  const SWAY = { G: 20, RISE: 0.4, WIN: 29.5, react: [0.2, 1.2], k: [0.75, 1.3], amp: [0.8, 1.2], gB: [0.55, 0.8],
                 P: [3.0, 5.4], osc: [0.30, 0.45], fP: [1.5, 2.3], fA: [0.07, 0.12], swP: [12, 15, 20, 30], swA: [0.12, 0.18],
                 inheritScale: 2.1 };   // amplitude boost for callers that pass no vars (scenes tuned to the old 0.9 % Pines); 1.9/0.9
  /* swayVars(seed, opts): the per-element variation. opts (all optional, Pines v3, Oct 18 2026 6:55 PM PT):
       tempo  N   -> the wind runs N times faster: N gusts per 30 s instead of 1, and the own-sway, flutter, swell periods
                     and reaction delays are divided by N. N must divide 30 so every gust window still ends on a loop
                     boundary (the loop stays exactly 60 s and every element is at rest at t = 0 and t = 60).
       gusts [..] -> strength of each gust window, in order (length 2*N). Shared by every element in the scene so the
                     whole forest feels the same strong and weak gusts; each element still jitters it by ±10 %.
     Without opts the result is exactly what it was before (other scenes and old clips are unchanged). */
  function swayVars(seed, opts) {
    const r = rngOf(seed), lerp = (a) => a[0] + (a[1] - a[0]) * r();
    const v = { react: lerp(SWAY.react), k: lerp(SWAY.k), amp: lerp(SWAY.amp), gB: lerp(SWAY.gB), P: lerp(SWAY.P), osc: lerp(SWAY.osc),
             ph: r() * TAU, fP: lerp(SWAY.fP), fA: lerp(SWAY.fA), fph: r() * TAU, swP: SWAY.swP[Math.floor(r() * 4)], swA: lerp(SWAY.swA) };
    const n = opts && opts.tempo;
    if (n && n !== 1) {
      v.tempo = n; v.win = 30 / n;                           // one gust per window; windows tile the loop exactly
      v.react /= n; v.P /= n; v.fP /= n; v.swP /= n;         // 12/15/20/30 s swell -> 6/7.5/10/15 s at 2x: all still divide 60
      const gs = opts.gusts || [];
      v.gs = []; for (let i = 0; i < 2 * n; i++) v.gs.push((gs[i] !== undefined ? gs[i] : (i % 2 ? v.gB : 1)) * (0.9 + 0.2 * r()));
    }
    return v;
  }
  function gustEnv(tau, D) {                    // one gust over D s: eased rise (40 %), damped eased return (60 %), then calm
    if (tau <= 0 || tau >= D) return 0; const R = SWAY.RISE * D;
    return tau < R ? 0.5 * (1 - Math.cos(Math.PI * tau / R)) : 0.5 * (1 + Math.cos(Math.PI * (tau - R) / (D - R)));
  }
  function swayLegacy(t, A, lam) {
    const u = ((t - lam) % L + L) % L;
    const g = u < 30 ? shapes.gust(u) : 0.65 * shapes.gust(u - 30);
    return A * (g + 0.15 * shapes.breathe(t, 20));
  }
  const varCache = new Map();
  function sway(t, A, lam, v) {
    if (api.legacySway) return swayLegacy(t, A, lam);
    if (v === undefined) A *= SWAY.inheritScale;             // old-amplitude callers (other scenes) get the 2:13 AM visibility boost
    if (v === undefined || typeof v === 'number') {
      const seed = v === undefined ? hashSeed(A, lam) : v;
      v = varCache.get(seed); if (!v) { v = swayVars(seed); varCache.set(seed, v); }
    }
    const d = Math.max(0, lam) + v.react;
    if (v.win) {                                               // tempo mode: 2*tempo shorter gusts per loop, each in its own window
      const W = v.win;
      const D = Math.min(SWAY.G / v.k / v.tempo, W - 0.5 - d); // gust (and its tail) ends 0.5 s before the window does
      const u = ((t - d) % L + L) % L;
      const wi = Math.floor(u / W), tau = u - W * wi;
      const g = gustEnv(tau, D) * v.gs[wi % v.gs.length];
      const osc = v.osc * Math.sin(TAU * tau / v.P + v.ph);
      const fl = v.fA * Math.sin(TAU * tau / v.fP + v.fph);
      return A * v.amp * (g * (1 + osc + fl) + v.swA * shapes.breathe(t, v.swP));
    }
    const D = Math.min(SWAY.G / v.k, SWAY.WIN - d);           // clamp so the gust (and its tail) ends inside the 30 s half
    const u = ((t - d) % L + L) % L;                           // element-local time; gust A in [0,30), gust B in [30,60)
    const half = u < 30 ? 0 : 1, tau = u - 30 * half;
    const g = gustEnv(tau, D) * (half ? v.gB : 1);
    const osc = v.osc * Math.sin(TAU * tau / v.P + v.ph);
    const fl = v.fA * Math.sin(TAU * tau / v.fP + v.fph);
    return A * v.amp * (g * (1 + osc + fl) + v.swA * shapes.breathe(t, v.swP));
  }
  function sample(fn, dt = DT) { const out = []; const n = Math.round(L / dt); for (let i = 0; i <= n; i++) { const k = fn(i * dt); if (dt !== DT) k.offset = i / n; out.push(k); } return out; }

  /* ---- 26.10.2 perf: one canvas, drawn in a Worker (OffscreenCanvas), instead of ~100 composited <img> layers ----
     The <img> layers and their Web Animations are still built exactly as before (they keep the clock: start, pause, seek,
     speed, loop boundaries). When the browser can draw on a canvas from a Worker, the pictures are handed to
     bg-canvas-worker.js once, the animations are kept only as the clock (no element to move), and the worker draws every
     frame off the main thread. Browsers without OffscreenCanvas (older Safari, old Firefox) keep the <img> layers.
     The page's full-window colour filter on #chat-bg (saturate) can be baked into the pictures once, so it no longer has to
     run over the whole window every frame; app.js then turns that filter off and gives the scrim the same baked colour.
     Frame 0 of the canvas is the same as the still, so the still can be swapped for the canvas in one step. */
  // the worker file sits next to this one and shares its cache key (?v=...)
  const SELF_URL = (document.currentScript && document.currentScript.src) || '/static/bganim/bg-anim.js';
  const WORKER_URL = SELF_URL.replace(/[^/]*$/, '') + 'bg-canvas-worker.js' + (/\?[^#]*/.exec(SELF_URL.split('/').pop()) || [''])[0];
  // 26.10.8 perf: the scene pictures were re-encoded (same names, ~69% smaller). Their URLs carry this file's cache key too,
  // so bumping BGA_V in app.js makes clients (and the service worker's cache-first /static/ copy) fetch the new pictures.
  const ASSET_Q = (/\?[^#]*/.exec(SELF_URL.split('/').pop()) || [''])[0];
  const withQ = (u) => (!u || !ASSET_Q || u.indexOf('?') >= 0 || u.indexOf('/static/bganim/') < 0) ? u : u + ASSET_Q;
  function canvasOK() {
    return !api.domOnly && typeof OffscreenCanvas === 'function' && typeof Worker === 'function' && typeof createImageBitmap === 'function' &&
      typeof HTMLCanvasElement.prototype.transferControlToOffscreen === 'function';
  }
  // Does this browser really apply a colour filter when drawing on a canvas? (Some accept the setting and ignore it.)
  const bakeCache = new Map();
  function bakeWorks(filter) {
    if (!filter || !/^saturate\([\d.]+%?\)$/.test(filter)) return false;
    if (bakeCache.has(filter)) return bakeCache.get(filter);
    let ok = false;
    try {
      const c = document.createElement('canvas'); c.width = c.height = 1; const g = c.getContext('2d', { willReadFrequently: true });
      g.filter = 'saturate(0)'; g.fillStyle = '#ff0000'; g.fillRect(0, 0, 1, 1);
      const d = g.getImageData(0, 0, 1, 1).data; ok = g.filter === 'saturate(0)' && d[0] < 120 && Math.abs(d[0] - d[1]) < 4;
    } catch (e) { ok = false; }
    bakeCache.set(filter, ok); return ok;
  }
  // A CSS transform string as function names + plain numbers (px, deg, or no unit), so the worker only blends numbers.
  // Returns null for anything it can't reproduce exactly (then the <img> layers stay).
  const ARITY = { translate: 2, translateX: 1, translateY: 1, scale: 2, scaleX: 1, scaleY: 1, rotate: 1, skewX: 1, skewY: 1, skew: 2, matrix: 6 };
  const IDENT = { translate: [0, 0], translateX: [0], translateY: [0], scale: [1, 1], scaleX: [1], scaleY: [1], rotate: [0], skewX: [0], skewY: [0], skew: [0, 0], matrix: [1, 0, 0, 1, 0, 0] };
  function parseTransform(str) {
    if (!str || str === 'none') return { names: [], args: [] };
    const names = [], args = []; const re = /([a-zA-Z]+)\(([^)]*)\)/g; let m;
    while ((m = re.exec(str))) {
      const name = m[1]; if (!ARITY[name]) return null;
      const vals = m[2].split(/[\s,]+/).filter(Boolean).map((a) => {
        const u = /^(-?[\d.]+(?:e-?\d+)?)(px|deg|rad|turn)?$/.exec(a); if (!u) return NaN;
        const v = +u[1]; return u[2] === 'rad' ? v * 180 / Math.PI : u[2] === 'turn' ? v * 360 : v;
      });
      if (vals.some(isNaN) || !vals.length) return null;
      if (name === 'translate' && vals.length === 1) vals.push(0);
      if (name === 'scale' && vals.length === 1) vals.push(vals[0]);
      if (name === 'skew' && vals.length === 1) vals.push(0);
      if (vals.length !== ARITY[name]) return null;
      names.push(name); args.push(...vals);
    }
    return names.length ? { names, args } : null;
  }
  // transform-origin as pixels inside the part (the browser's default for an <img> is its centre)
  function originOf(o, w, h) {
    const kw = { left: '0%', center: '50%', right: '100%', top: '0%', bottom: '100%' };
    const v = String(o || '50% 50%').trim().split(/\s+/).map((a) => kw[a] || a);
    if (v.length === 1) v.push('50%');
    const px = (a, size) => a.endsWith('%') ? parseFloat(a) / 100 * size : parseFloat(a);
    return [px(v[0], w), px(v[1], h)];
  }
  // Everything the worker needs, from the animations the browser has already built (so timing and offsets are identical).
  function canvasPlan(scene) {
    const plan = [];
    for (let i = 0; i < scene.els.length; i++) {
      const p = scene.def.parts[i], a = scene.animOf[i], init = p.initial || {};
      if (Object.keys(init).some((k) => k !== 'opacity')) return null;
      const [ox, oy] = originOf(p.origin, p.w, p.h);
      const q = { x: p.x, y: p.y, w: p.w, h: p.h, ox, oy, op0: init.opacity != null ? +init.opacity : 1, offs: null, ops: null, names: null, arity: null, args: null };
      if (a) {
        const kf = a.effect.getKeyframes();
        const props = new Set(); kf.forEach((k) => Object.keys(k).forEach((n) => { if (!['offset', 'computedOffset', 'easing', 'composite'].includes(n)) props.add(n); }));
        if ([...props].some((n) => n !== 'opacity' && n !== 'transform')) return null;
        if (kf.some((k) => (k.easing && k.easing !== 'linear') || (k.composite && k.composite !== 'auto' && k.composite !== 'replace'))) return null;
        q.offs = kf.map((k) => k.computedOffset);
        if (props.has('opacity')) { if (kf.some((k) => k.opacity == null)) return null; q.ops = kf.map((k) => +k.opacity); }
        if (props.has('transform')) {
          const pt = kf.map((k) => parseTransform(k.transform));
          if (pt.some((t) => !t)) return null;
          const ref = pt.find((t) => t.names.length);
          if (ref) {
            for (const t of pt) {   // 'none' keyframes become the matching do-nothing values; any other mismatch -> keep <img> layers
              if (!t.names.length) { t.names = ref.names; t.args = [].concat(...ref.names.map((n) => IDENT[n])); }
              else if (t.names.join() !== ref.names.join()) return null;
            }
            q.names = ref.names; q.arity = ref.names.map((n) => ARITY[n]); q.args = pt.map((t) => t.args);
          }
        }
      }
      plan.push(q);
    }
    return plan;
  }

  const prefs = {
    get anim() { return localStorage.getItem('bc-bg-anim') === '1'; },
    set anim(v) { localStorage.setItem('bc-bg-anim', v ? '1' : '0'); },
    get speed() { const v = parseFloat(localStorage.getItem('bc-bg-speed')); return [0.5, 1, 2].includes(v) ? v : 1; },
    set speed(v) { localStorage.setItem('bc-bg-speed', String(Math.min(2, Math.max(0.5, v)))); },
  };
  const mqReduce = matchMedia('(prefers-reduced-motion: reduce)');
  const blocked = () => mqReduce.matches || !!(navigator.connection && navigator.connection.saveData) || (navigator.deviceMemory && navigator.deviceMemory <= 2);

  /* Scene: host element, scene definition object (see README: {W,H,still,parts:[{src,x,y,w,h,origin,keyframes()}]}) */
  class Scene {
    constructor(host, def) {
      this.host = host; this.def = def; this.anims = []; this.els = []; this.state = 'STATIC';
      host.classList.add('bga-host');
      this.stage = document.createElement('div'); this.stage.className = 'bga-stage';
      this.stage.style.width = def.W + 'px'; this.stage.style.height = def.H + 'px';
      this.still = new Image(); this.still.className = 'bga-still'; this.still.alt = '';
      this.still.src = withQ(def.still); Object.assign(this.still.style, { left: 0, top: 0, width: def.W + 'px', height: def.H + 'px' });
      this.stage.appendChild(this.still); host.appendChild(this.stage);
      this.fit(); this._ro = new ResizeObserver(() => this.fit()); this._ro.observe(host);
      this.speed = prefs.speed; this.pendingSpeed = null; this.loops = 0;
      this.animOf = [];   // part index -> its animation (26.10.2 canvas painter)
      this.bake = null; this.onBake = null;   // set by app.js: the page colour filter to bake in, and how to switch it off
      api.scenes.add(this);   // 26.10.8: so a Lite mode switch can re-send the frame rate
    }
    fit() {   // cover, centred horizontally, top-aligned (same as the app's background-position: 50% 0)
      const w = this.host.clientWidth, h = this.host.clientHeight, k = Math.max(w / this.def.W, h / this.def.H);
      // 26.10.4 fix: a scene may ask to sit centred vertically instead (def.alignY = 0.5, used by Fourth of July, whose
      // still is shown with the app's normal background-position: center). Every other scene leaves it out: top-aligned.
      const ty = (h - this.def.H * k) * (this.def.alignY || 0);
      this.stage.style.transform = `translate(${(w - this.def.W * k) / 2}px, ${ty}px) scale(${k})`;
      // 26.10.8: Lite mode (html.lite) draws the moving scene at 30 frames a second, at most 1.5x device pixels
      const lite = document.documentElement.classList.contains('lite');
      if (this._w) this._w.postMessage({ view: { w, h, k, tx: (w - this.def.W * k) / 2, ty, dpr: lite ? Math.min(1.5, window.devicePixelRatio || 1) : (window.devicePixelRatio || 1), quality: api.canvasQuality, fps: lite ? 30 : 0 } });
    }
    async mount() {
      const imgs = this.def.parts.map(p => {
        const im = new Image(); im.alt = ''; im.decoding = 'async'; im.src = withQ(p.src);
        Object.assign(im.style, { left: p.x + 'px', top: p.y + 'px', width: p.w + 'px', height: p.h + 'px', zIndex: 1 });
        if (p.origin) im.style.transformOrigin = p.origin;
        if (p.initial) Object.assign(im.style, p.initial);
        return im;
      });
      await Promise.race([Promise.all(imgs.map(i => i.decode())), new Promise((_, r) => setTimeout(() => r(new Error('decode timeout')), 10000))]);
      imgs.forEach((im, i) => {
        this.stage.insertBefore(im, this.still); this.els.push(im);
        const kf = this.def.parts[i].keyframes();
        if (!kf) return;
        im.style.willChange = Object.keys(kf[0]).filter(k => k !== 'offset').join(',');
        const a = im.animate(kf, { duration: L * 1000, iterations: Infinity, easing: 'linear' });
        a.pause(); a.currentTime = 0; this.anims.push(a); this.animOf[i] = a;
      });
    }
    async start() {                                        // 6.1
      if (this.state !== 'STATIC' || blocked()) return;
      this.state = 'HANDOFF_IN';
      try { await this.mount(); } catch (e) { this.unmount(); this.state = 'STATIC'; return; }
      let painter = false;   // 26.10.2 perf: one canvas drawn in a Worker, when the browser can (see canvasPlan)
      if (canvasOK()) { try { painter = await this._canvasStart(); } catch (e) { painter = false; } if (!painter) this._canvasEnd(); }
      if (this.state !== 'HANDOFF_IN') { this._canvasEnd(); return; }   // stopped or destroyed while getting ready
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      this.setRate(prefs.speed);
      const t0 = document.timeline.currentTime;
      this.anims.forEach(a => { a.startTime = t0; });
      this.ref = this.anims[0];
      if (painter) {
        // The canvas already shows frame 0, which is the still, so swap in one step: the animations only keep time now
        // (no element to move), the <img> layers leave the page, the still hides, and the baked colour filter goes off.
        this.anims.forEach(a => { a.effect.target = null; });
        this.els.forEach(e => e.remove());
        this.still.style.transition = 'none'; this.still.classList.add('bga-hidden'); void this.still.offsetWidth; this.still.style.transition = '';
        if (this._baked && this.onBake) this.onBake(true);
        this._post();
      } else this.still.classList.add('bga-hidden');
      this.state = 'LIVE';
      this._tick();
    }
    setRate(s) { this.speed = s; this.anims.forEach(a => a.playbackRate = s); }
    elapsed() { return this.ref ? this.ref.currentTime / 1000 : 0; }          // in loop-seconds (rate already applied)
    _tick() {   // loop-boundary hook: apply pending speed exactly at frame 0
      if (this.state !== 'LIVE' && this.state !== 'STOPPING') return;
      const e = this.elapsed(), n = Math.floor(e / L);
      if (n !== this.loops) {
        this.loops = n;
        if (this.pendingSpeed && this.pendingSpeed !== this.speed) {
          const T = n * L * 1000; this.anims.forEach(a => { a.currentTime = T; });
          const s = this.pendingSpeed; this.pendingSpeed = null;
          this.anims.forEach(a => { a.pause(); a.currentTime = T; a.playbackRate = s; a.play(); }); this.speed = s;
        }
      }
      if (this._w) this._post();   // tell the canvas painter if the clock changed (pause, play, speed)
      this._raf = requestAnimationFrame(() => this._tick());
    }
    requestSpeed(s) { this.pendingSpeed = s; }
    stop(instant) {                                        // 6.2
      if (this.state === 'STATIC') return Promise.resolve();
      if (instant || document.hidden || blocked()) { this._finish(true); return Promise.resolve(); }
      this.state = 'STOPPING';
      return new Promise(res => {
        const e = this.elapsed(); const T = (e % L < 0.05) ? e : Math.ceil(e / L) * L;
        const watch = () => {
          if (this.state !== 'STOPPING') return res();
          if (this.elapsed() >= T - 0.008) {
            this.anims.forEach(a => { a.pause(); a.currentTime = T * 1000; });
            this._finish(false).then(res); return;
          }
          requestAnimationFrame(watch);
        };
        watch();
        this._fallback = setTimeout(() => { if (this.state === 'STOPPING') { this._finish(true); res(); } }, ((T - e) / this.speed) * 1000 + 500);
      });
    }
    cancelStop() { if (this.state === 'STOPPING') { clearTimeout(this._fallback); this.state = 'LIVE'; } }
    _finish(instant) {
      clearTimeout(this._fallback); this.state = 'HANDOFF_OUT';
      const done = () => { this.unmount(); this.state = 'STATIC'; };
      if (this._w) {
        // The clock is stopped on the loop's last frame, which is the still: show the still at once, then drop the canvas
        // and put the page's colour filter back in the same step, so nothing visibly changes.
        this.still.style.transition = 'none'; this.still.classList.remove('bga-hidden'); void this.still.offsetWidth; this.still.style.transition = '';
        this._canvasEnd();
      }
      if (instant) { this.still.style.transition = 'none'; this.still.classList.remove('bga-hidden'); void this.still.offsetWidth; this.still.style.transition = ''; done(); return Promise.resolve(); }
      return new Promise(res => {
        this.still.classList.remove('bga-hidden');
        const dur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bc-dur-bg')) || 0;
        setTimeout(() => { done(); res(); }, dur + 50);
      });
    }
    unmount() {
      this._canvasEnd();
      cancelAnimationFrame(this._raf);
      this.anims.forEach(a => a.cancel()); this.anims = []; this.ref = null;
      this.els.forEach(e => e.remove()); this.els = []; this.loops = 0;
    }
    /* review/test hook: show the animated stack paused at loop time t (seconds); still hidden */
    async seek(t) {
      if (!this.anims.length) { await this.mount(); }
      this.anims.forEach(a => { a.pause(); a.currentTime = t * 1000; });
      this.still.style.transition = 'none'; this.still.classList.add('bga-hidden'); this.state = 'SEEK';
      if (this._w) await this._post(true);   // wait until the canvas shows that exact moment
    }
    /* ---- canvas painter (26.10.2 perf) ---- */
    async _canvasStart() {
      const plan = canvasPlan(this);
      if (!plan) return false;
      this._baked = !!(this.bake && bakeWorks(this.bake));
      // each picture once (parts that share a file share it), with the colour filter baked in when that works
      const bySrc = new Map();
      const bitmapOf = async (im) => {
        if (bySrc.has(im.src)) return bySrc.get(im.src);
        // decoded through createImageBitmap: exactly the pixels the page shows for the <img>. The colour filter is baked in
        // by the worker itself (see bg-canvas-worker.js), straight from these pictures.
        const b = await createImageBitmap(im);
        bySrc.set(im.src, b); return b;
      };
      for (let i = 0; i < plan.length; i++) plan[i].bmp = await bitmapOf(this.els[i]);
      const cv = document.createElement('canvas'); cv.className = 'bga-canvas'; cv.setAttribute('aria-hidden', 'true');
      Object.assign(cv.style, { position: 'absolute', left: 0, top: 0, width: '100%', height: '100%', display: 'block', pointerEvents: 'none' });
      this.host.insertBefore(cv, this.stage);   // under the stage, so the still (inside the stage) covers it until the swap
      const off = cv.transferControlToOffscreen();
      this._cv = cv; this._w = new Worker(WORKER_URL); this._acks = new Map(); this._ackN = 0; this._sig = '';
      this._w.onmessage = (e) => { const r = this._acks.get(e.data.drawn); if (r) { this._acks.delete(e.data.drawn); r(); } };
      const failed = new Promise((_, rej) => { this._w.onerror = () => rej(new Error('worker failed')); });
      this._w.postMessage({ init: true, canvas: off, parts: plan, L: L * 1000, bake: this._baked ? this.bake : null }, [off, ...new Set(plan.map((q) => q.bmp))]);
      this.fit();
      this._vis = () => this._post();   // the app pauses / resumes the clock when the tab is hidden / shown
      document.addEventListener('visibilitychange', this._vis);
      await Promise.race([this._post(true), failed, new Promise((_, rej) => setTimeout(() => rej(new Error('canvas timeout')), 5000))]);
      return true;
    }
    // Send the clock to the worker if it changed (or always, with wait=true, and resolve once that frame is drawn).
    _post(wait) {
      if (!this._w || !this.anims.length) return Promise.resolve();
      const a = this.anims[0], paused = a.playState !== 'running', tl = document.timeline.currentTime;
      const sig = paused ? 'p' + a.currentTime + '/' + a.playbackRate : 'r' + a.startTime + '/' + a.playbackRate;
      if (!wait && sig === this._sig) return Promise.resolve();
      this._sig = sig;
      // running: the loop time at this frame's timeline moment (the same moment the browser animates to)
      const clock = paused ? { paused: true, cur: a.currentTime || 0, rate: a.playbackRate, at: 0 }
                           : { paused: false, cur: (tl - a.startTime) * a.playbackRate, rate: a.playbackRate, at: performance.timeOrigin + tl };
      if (!wait) { this._w.postMessage({ clock }); return Promise.resolve(); }
      const id = ++this._ackN;
      return new Promise((res) => { this._acks.set(id, res); this._w.postMessage({ clock, ack: id }); });
    }
    _canvasEnd() {
      if (this._vis) { document.removeEventListener('visibilitychange', this._vis); this._vis = null; }
      if (this._w) { try { this._w.postMessage({ stop: true }); } catch (e) { /* gone */ } this._w.terminate(); this._w = null; }
      if (this._cv) { this._cv.remove(); this._cv = null; }
      if (this._baked && this.onBake) this.onBake(false);
      this._baked = false;
      if (this._acks) { this._acks.forEach((r) => r()); this._acks.clear(); }
    }
    showStill() { this.still.style.transition = 'none'; this.still.classList.remove('bga-hidden'); }
    destroy() { this.unmount(); this._ro.disconnect(); this.stage.remove(); api.scenes.delete(this); }
  }

  /* Controller: binds the switch + speed row to localStorage, reduced motion, cross-tab, visibility. */
  function controller(getScene, ui) {
    const sync = () => {
      const b = blocked();
      if (ui) {
        ui.sw.setAttribute('aria-checked', String(prefs.anim && !b)); ui.sw.disabled = b;
        if (ui.note) ui.note.textContent = mqReduce.matches ? "Off while your device's Reduce Motion setting is on" : '';
        ui.speedBtns.forEach(btn => { btn.disabled = !prefs.anim || b; btn.setAttribute('aria-pressed', String(parseFloat(btn.dataset.speed) === prefs.speed)); });
      }
    };
    const apply = (instant) => {
      const s = getScene(); sync(); if (!s) return;
      if (prefs.anim && !blocked()) { if (s.state === 'STOPPING') s.cancelStop(); else s.start(); }
      else {
        if (ui && ui.note && s.state === 'LIVE') ui.note.textContent = 'Finishing the current loop…';
        s.stop(instant).then(sync);
      }
    };
    if (ui) {
      ui.sw.addEventListener('click', () => { prefs.anim = !prefs.anim; apply(false); });
      ui.speedBtns.forEach(btn => btn.addEventListener('click', () => { prefs.speed = parseFloat(btn.dataset.speed); const s = getScene(); if (s) s.requestSpeed(prefs.speed); sync(); }));
    }
    mqReduce.addEventListener('change', () => apply(true));
    addEventListener('storage', e => { if (e.key === 'bc-bg-anim') apply(false); if (e.key === 'bc-bg-speed') { const s = getScene(); if (s) s.requestSpeed(prefs.speed); sync(); } });
    document.addEventListener('visibilitychange', () => { const s = getScene(); if (!s) return; s.anims.forEach(a => document.hidden ? a.pause() : (s.state === 'LIVE' && a.play())); });
    apply(true);
    return { apply, sync };
  }

  const api = { scenes: new Set(), canvasQuality: 'low', domOnly: false, L, DT, shapes, sway, swayLegacy, swayVars, gustEnv, rngOf, SWAY, legacySway: false, sample, prefs, blocked, Scene, controller };
  window.BgAnim = api;
})();
