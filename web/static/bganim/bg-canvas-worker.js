/* Branch Chat animated backgrounds: the scene painter that runs off the main thread (26.10.2 perf).
   Instead of ~100 separately composited <img> layers, every moving part of a scene is drawn into ONE canvas here, in a
   Worker, so the page itself (scrolling, typing, the glass bubbles) never waits for the background.
   bg-anim.js sends, once: the canvas, each part's picture (already decoded, and already colour-adjusted when the page's
   colour filter could be baked in), its position, its turning point and its keyframes as plain numbers. After that it
   only sends "where is the clock" (when the scene starts, pauses, seeks or changes speed) and "the window changed size".
   Drawing follows exactly the same rules as the browser's own animation of the <img> layers: keyframes at their
   offsets, numbers blended in a straight line between neighbours, every transform turned around the part's own origin,
   opacity as the part's see-through-ness. Only moves and fades: no per-part filters, so it stays cheap. */
'use strict';
let cv = null, g = null, parts = [], L = 60000, view = null, clock = null, raf = 0, ackId = 0;
const now = () => performance.timeOrigin + performance.now();   // the same clock as the page (both count from 1970)
const nextFrame = self.requestAnimationFrame ? (f) => self.requestAnimationFrame(f) : (f) => setTimeout(f, 16);
const cancelFrame = self.cancelAnimationFrame ? (id) => self.cancelAnimationFrame(id) : (id) => clearTimeout(id);

// Where the loop is now, in milliseconds (the page's animation clock, moved forward by the time since it was sent).
function loopTime() {
  if (!clock) return 0;
  const t = clock.paused ? clock.cur : clock.cur + (now() - clock.at) * clock.rate;
  return ((t % L) + L) % L;
}
// Which two keyframes the time falls between, and how far along (0..1). offs is sorted 0..1.
function locate(offs, p) {
  let lo = 0, hi = offs.length - 1;
  if (p <= offs[0]) return [0, 0, 0];
  if (p >= offs[hi]) return [hi, hi, 0];
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (offs[mid] <= p) lo = mid; else hi = mid; }
  const span = offs[hi] - offs[lo];
  return [lo, hi, span > 0 ? (p - offs[lo]) / span : 0];
}
// Multiply the 2D matrix m=[a,b,c,d,e,f] by one CSS transform function (same maths as the browser).
function apply(m, name, v) {
  let n;
  switch (name) {
    case 'translate': n = [1, 0, 0, 1, v[0], v[1]]; break;
    case 'translateX': n = [1, 0, 0, 1, v[0], 0]; break;
    case 'translateY': n = [1, 0, 0, 1, 0, v[0]]; break;
    case 'scale': n = [v[0], 0, 0, v[1], 0, 0]; break;
    case 'scaleX': n = [v[0], 0, 0, 1, 0, 0]; break;
    case 'scaleY': n = [1, 0, 0, v[0], 0, 0]; break;
    case 'rotate': { const r = v[0] * Math.PI / 180, c = Math.cos(r), s = Math.sin(r); n = [c, s, -s, c, 0, 0]; break; }
    case 'skewX': n = [1, 0, Math.tan(v[0] * Math.PI / 180), 1, 0, 0]; break;
    case 'skewY': n = [1, Math.tan(v[0] * Math.PI / 180), 0, 1, 0, 0]; break;
    case 'skew': n = [1, Math.tan(v[1] * Math.PI / 180), Math.tan(v[0] * Math.PI / 180), 1, 0, 0]; break;
    case 'matrix': n = v; break;
    default: return m;
  }
  return [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
          m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
}
function draw() {
  if (!g || !view) return;
  const W = Math.round(view.w * view.dpr), H = Math.round(view.h * view.dpr);
  if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; g.imageSmoothingEnabled = true; g.imageSmoothingQuality = view.quality || 'low'; }
  const p = loopTime() / L;
  g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = 1; g.clearRect(0, 0, W, H);
  // the stage: cover the window, centred, top-aligned, at device pixels (same as Scene.fit)
  const s = view.dpr * view.k, base = [s, 0, 0, s, view.dpr * view.tx, view.dpr * (view.ty || 0)];   // ty: 26.10.4, 0 unless the scene is centred vertically
  for (const q of parts) {
    let op = q.op0, m = apply(base, 'translate', [q.x, q.y]);
    if (q.offs) {
      const [i, j, f] = locate(q.offs, p);
      if (q.ops) op = q.ops[i] + (q.ops[j] - q.ops[i]) * f;
      if (q.names) {
        m = apply(m, 'translate', [q.ox, q.oy]);
        const A = q.args[i], B = q.args[j];
        let k = 0;
        for (let fi = 0; fi < q.names.length; fi++) {
          const ar = q.arity[fi], v = new Array(ar);
          for (let a = 0; a < ar; a++, k++) v[a] = A[k] + (B[k] - A[k]) * f;
          m = apply(m, q.names[fi], v);
        }
        m = apply(m, 'translate', [-q.ox, -q.oy]);
      }
    }
    op = Math.min(1, Math.max(0, op));
    if (op <= 0) continue;                       // invisible this frame: nothing to draw
    g.globalAlpha = op; g.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
    g.drawImage(q.src, 0, 0, q.w, q.h);
  }
}
// 26.10.8 Lite mode: view.fps (e.g. 30) caps how often a frame is drawn; 0 = every display frame as before.
let lastDraw = 0;
function loop() {
  raf = 0;
  const iv = view && view.fps ? 1000 / view.fps : 0, t = performance.now();
  if (!iv || t - lastDraw >= iv - 4) { lastDraw = t; draw(); }
  if (clock && !clock.paused) raf = nextFrame(loop);
}
// Something changed (clock, size): draw on the next frame. While the clock is paused (a test seek, or the tab is being
// hidden) the same frame is drawn a few more times, so a frame the browser happened to skip showing is put right.
let repaint = 0;
function settle() { raf = 0; draw(); if (--repaint > 0) raf = nextFrame(settle); }
function kick() {
  if (raf) cancelFrame(raf);
  raf = nextFrame(() => {
    raf = 0; draw();
    if (ackId) { postMessage({drawn: ackId}); ackId = 0; }
    if (clock && !clock.paused) raf = nextFrame(loop);
    else { repaint = 3; raf = nextFrame(settle); }
  });
}
onmessage = (e) => {
  const m = e.data;
  if (m.init) {
    cv = m.canvas; g = cv.getContext('2d'); parts = m.parts; L = m.L;
    if (m.bake) {
      // Bake the page's colour filter (e.g. saturate(0.9)) into each picture once, on a canvas of its own that is then
      // drawn every frame. Pictures shared by several parts arrive as the same object, so each is baked only once.
      // (Baking here, rather than turning a canvas back into a picture on the page, keeps every picture reliably intact.)
      const baked = new Map();
      for (const q of parts) {
        let c = baked.get(q.bmp);
        if (!c) {
          c = new OffscreenCanvas(q.bmp.width, q.bmp.height);
          const cg = c.getContext('2d'); cg.filter = m.bake; cg.drawImage(q.bmp, 0, 0);
          baked.set(q.bmp, c);
        }
        q.src = c;
      }
      baked.forEach((c, bmp) => bmp.close());
    } else parts.forEach((q) => { q.src = q.bmp; });
  }
  if (m.view) view = m.view;
  if (m.clock) clock = m.clock;
  if (m.ack) ackId = m.ack;
  if (m.stop) { if (raf) cancelFrame(raf); raf = 0; parts.forEach((q) => { if (q.bmp.close) q.bmp.close(); }); parts = []; g = null; close(); return; }
  kick();
};
