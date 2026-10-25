/* 26.10.12: send quality (Original / HD 1080p / SD 720p), photo editor (crop, rotate, brightness/contrast/saturation),
   video editor (trim start/end, mute), and pinch/double-tap/wheel zoom in the full-screen viewer.
   Plain canvas + DOM, no libraries. */

const QKEY = "bc.sendQuality";
export const QUALITIES = ["original", "hd", "sd"];
export const QLABEL = {original: "Original", hd: "HD", sd: "SD"};
export const QLONG = {original: "Original quality", hd: "HD 1080p", sd: "SD 720p"};
export function getQuality() { try { const q = localStorage.getItem(QKEY); if (QUALITIES.includes(q)) return q; } catch (e) {} return "hd"; }
export function setQuality(q) { if (QUALITIES.includes(q)) try { localStorage.setItem(QKEY, q); } catch (e) {} }

function loadImg(blob) {
  return new Promise((ok, bad) => {
    const u = URL.createObjectURL(blob), i = new Image();
    i.onload = () => ok({img: i, url: u});
    i.onerror = () => { URL.revokeObjectURL(u); bad(new Error("That photo could not be opened.")); };
    i.src = u;
  });
}
const toBlob = (c, type, q) => new Promise((r) => c.toBlob(r, type, q));

/* Photo quality: HD = long edge 2048 (short ≥1080 equivalent), SD = 1280. Original is untouched.
   Returns the original blob when it is already small enough or can't be drawn. */
export async function resizePhoto(blob, quality) {
  if (quality === "original" || !blob || /gif|svg|heic|heif/i.test(blob.type || "")) return blob;
  const long = quality === "sd" ? 1280 : 2048;
  let L;
  try { L = await loadImg(blob); } catch (e) { return blob; }
  const {img, url} = L;
  const w = img.naturalWidth, h = img.naturalHeight, s = Math.min(1, long / Math.max(w, h));
  if (s >= 1 && blob.size < 1.5 * 1024 * 1024) { URL.revokeObjectURL(url); return blob; }
  const c = document.createElement("canvas");
  c.width = Math.round(w * s); c.height = Math.round(h * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  URL.revokeObjectURL(url);
  const png = /png/i.test(blob.type) && blob.size < 4e6;
  const out = await toBlob(c, png ? "image/png" : "image/jpeg", 0.9);
  c.width = c.height = 0;
  return out && out.size < blob.size ? out : blob;
}

/* ---------- small dialog helper ---------- */
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, ""); else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
function dialog(title) {
  const d = el("dialog", {class: "medit", "aria-label": title});
  document.body.append(d);
  d.addEventListener("close", () => setTimeout(() => d.remove(), 0));
  return d;
}

/* ---------- photo editor ---------- */
const RATIOS = [["Free", 0], ["1:1", 1], ["4:3", 4 / 3], ["3:4", 3 / 4], ["16:9", 16 / 9], ["9:16", 9 / 16]];

/* Same math as CSS brightness()/contrast()/saturate() so the preview and the result match. b,c,s are 0..2 (1 = none). */
export function adjustPixels(data, b, c, s) {
  if (b === 1 && c === 1 && s === 1) return;
  const ic = 255 * (0.5 - 0.5 * c);
  const r0 = 0.213 + 0.787 * s, r1 = 0.715 - 0.715 * s, r2 = 0.072 - 0.072 * s;
  const g0 = 0.213 - 0.213 * s, g1 = 0.715 + 0.285 * s, g2 = 0.072 - 0.072 * s;
  const b0 = 0.213 - 0.213 * s, b1 = 0.715 - 0.715 * s, b2 = 0.072 + 0.928 * s;
  for (let i = 0; i < data.length; i += 4) {
    let R = data[i] * b, G = data[i + 1] * b, B = data[i + 2] * b;
    R = R * c + ic; G = G * c + ic; B = B * c + ic;
    data[i] = R * r0 + G * r1 + B * r2;
    data[i + 1] = R * g0 + G * g1 + B * g2;
    data[i + 2] = R * b0 + G * b1 + B * b2;
  }
}

/* Resolves to an edited File, or null if cancelled. */
const ICONS = {b: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/></svg>', c: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>', s: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3c4 5 6 8 6 11a6 6 0 0 1-12 0c0-3 2-6 6-11z"/></svg>', v: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="5"/></svg>'};   // 26.10.13 editor icons
/* 26.10.13: vignette, v in -1..1. Positive darkens the edges, negative whitens them; strength = |v|. */
export function vignette(x, w, h, v) {
  if (!v || !w || !h) return;
  const r = Math.hypot(w, h) / 2, g = x.createRadialGradient(w / 2, h / 2, r * 0.35, w / 2, h / 2, r);
  const c = v > 0 ? "0,0,0" : "255,255,255";
  g.addColorStop(0, "rgba(" + c + ",0)"); g.addColorStop(1, "rgba(" + c + "," + (Math.min(1, Math.abs(v)) * 0.9).toFixed(3) + ")");
  x.save(); x.fillStyle = g; x.fillRect(0, 0, w, h); x.restore();
}
export async function editPhoto(file) {
  const {img, url} = await loadImg(file);
  const st = {rot: 0, ratio: 0, b: 100, c: 100, s: 100, v: 0, crop: {x: 0, y: 0, w: 1, h: 1}};   // crop is in rotated-image fractions
  const d = dialog("Edit photo");
  const stage = el("div", {class: "medit-stage"});
  const cv = el("canvas", {class: "medit-canvas"});
  const box = el("div", {class: "medit-crop", "data-testid": "crop-box"});
  for (const h of ["nw", "ne", "sw", "se"]) box.append(el("span", {class: "medit-h medit-" + h, "data-h": h}));
  const vcv = el("canvas", {class: "medit-vig", "aria-hidden": "true"});   // 26.10.13: live vignette preview
  stage.append(cv, vcv, box);
  const ratioBar = el("div", {class: "medit-row medit-ratios", role: "group", "aria-label": "Crop shape"});
  for (const [n, r] of RATIOS) ratioBar.append(el("button", {type: "button", class: "chip", "data-ratio": n, "aria-pressed": r === 0 ? "true" : "false", onclick: () => setRatio(r, n)}, n));
  const slider = (label, key, min = "0", max = "200", def = "100") => {
    // 26.10.13: header = icon + label + live whole-number value (0 = unchanged); one slider, step 1
    const i = el("input", {type: "range", min, max, step: "1", value: def, "data-def": def, "aria-label": label, "data-adj": key});
    const val = el("output", {class: "medit-val"}, "0");
    const show = () => { const n = Math.round(i.value) - Math.round(def); val.textContent = n > 0 ? "+" + n : String(n); };
    i.addEventListener("input", () => { st[key] = Math.round(i.value); show(); paintFilter(); });
    i.bcShow = show;
    const ico = el("span", {class: "medit-ico", "aria-hidden": "true"}); ico.innerHTML = ICONS[key] || "";
    return el("label", {class: "medit-slider"}, el("span", {class: "medit-head"}, ico, el("span", {class: "medit-name"}, label), val), i);
  };
  const tools = el("div", {class: "medit-row"},
    el("button", {type: "button", class: "ghost", "data-act": "rotl", "aria-label": "Rotate left", onclick: () => rotate(-1)}, "\u27F2 Rotate"),
    el("button", {type: "button", class: "ghost", "data-act": "rotr", "aria-label": "Rotate right", onclick: () => rotate(1)}, "\u27F3"),
    el("button", {type: "button", class: "ghost", "data-act": "reset", onclick: () => reset()}, "Reset"));
  const adj = el("div", {class: "medit-adj"}, slider("Brightness", "b"), slider("Contrast", "c"), slider("Saturation", "s"), slider("Vignette", "v", "-100", "100", "0"));
  const done = el("button", {type: "button", class: "solid", "data-act": "done"}, "Done");
  const cancel = el("button", {type: "button", class: "ghost", "data-act": "cancel"}, "Cancel");
  d.append(el("h2", {}, "Edit photo"), stage, ratioBar, tools, adj, el("div", {class: "actions"}, cancel, done));

  const rw = () => (st.rot % 2 ? img.naturalHeight : img.naturalWidth), rh = () => (st.rot % 2 ? img.naturalWidth : img.naturalHeight);
  function drawBase() {
    const maxW = Math.min(stage.clientWidth || 600, 900), maxH = Math.max(160, Math.min(window.innerHeight * 0.5, 560));
    const k = Math.min(maxW / rw(), maxH / rh(), 1);
    cv.width = Math.round(rw() * k); cv.height = Math.round(rh() * k);
    const x = cv.getContext("2d");
    x.save(); x.translate(cv.width / 2, cv.height / 2); x.rotate(st.rot * Math.PI / 2);
    const w = st.rot % 2 ? cv.height : cv.width, h = st.rot % 2 ? cv.width : cv.height;
    x.drawImage(img, -w / 2, -h / 2, w, h); x.restore();
    paintFilter(); paintBox();
  }
  function paintFilter() {
    cv.style.filter = `brightness(${st.b / 100}) contrast(${st.c / 100}) saturate(${st.s / 100})`;
    vcv.width = cv.width; vcv.height = cv.height;   // resizing clears it
    Object.assign(vcv.style, {left: cv.offsetLeft + "px", top: cv.offsetTop + "px", width: cv.offsetWidth + "px", height: cv.offsetHeight + "px"});
    vignette(vcv.getContext("2d"), vcv.width, vcv.height, (st.v || 0) / 100);
  }
  function paintBox() {
    const c = st.crop;
    Object.assign(box.style, {left: cv.offsetLeft + c.x * cv.width + "px", top: cv.offsetTop + c.y * cv.height + "px", width: c.w * cv.width + "px", height: c.h * cv.height + "px"});
  }
  function fitRatio() {
    if (!st.ratio) return;
    const ar = st.ratio * rh() / rw();   // in fraction space
    let w = st.crop.w, h = w / ar;
    if (h > 1) { h = 1; w = ar; }
    if (w > 1) { w = 1; h = 1 / ar; }
    const cx = st.crop.x + st.crop.w / 2, cy = st.crop.y + st.crop.h / 2;
    st.crop = {w, h, x: Math.min(1 - w, Math.max(0, cx - w / 2)), y: Math.min(1 - h, Math.max(0, cy - h / 2))};
  }
  function setRatio(r, n) {
    st.ratio = r; if (r) st.crop = {x: 0, y: 0, w: 1, h: 1};
    fitRatio(); paintBox();
    for (const b of ratioBar.children) b.setAttribute("aria-pressed", b.dataset.ratio === n ? "true" : "false");
  }
  function rotate(dir) { st.rot = (st.rot + dir + 4) % 4; st.crop = {x: 0, y: 0, w: 1, h: 1}; fitRatio(); drawBase(); }
  function reset() { st.rot = 0; st.b = st.c = st.s = 100; st.v = 0; for (const i of adj.querySelectorAll("input")) { i.value = i.dataset.def; i.bcShow && i.bcShow(); } setRatio(0, "Free"); drawBase(); }

  // drag the box (move) or a corner (resize); pointer events work for mouse, touch and pen
  let drag = null;
  box.addEventListener("pointerdown", (e) => {
    e.preventDefault(); try { box.setPointerCapture(e.pointerId); } catch (x) {}
    drag = {h: e.target.dataset.h || "", x: e.clientX, y: e.clientY, c: {...st.crop}};
  });
  box.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = (e.clientX - drag.x) / cv.width, dy = (e.clientY - drag.y) / cv.height, o = drag.c, MIN = 0.05;
    let {x, y, w, h} = o;
    if (!drag.h) { x = Math.min(1 - w, Math.max(0, o.x + dx)); y = Math.min(1 - h, Math.max(0, o.y + dy)); }
    else {
      const L = drag.h.includes("w"), T = drag.h.includes("n");
      let nx = L ? Math.max(0, Math.min(o.x + o.w - MIN, o.x + dx)) : o.x;
      let nr = L ? o.x + o.w : Math.min(1, Math.max(o.x + MIN, o.x + o.w + dx));
      let ny = T ? Math.max(0, Math.min(o.y + o.h - MIN, o.y + dy)) : o.y;
      let nb = T ? o.y + o.h : Math.min(1, Math.max(o.y + MIN, o.y + o.h + dy));
      w = nr - nx; h = nb - ny;
      if (st.ratio) {
        const ar = st.ratio * rh() / rw();
        h = w / ar;
        if (T) ny = nb - h; if (ny < 0 || ny + h > 1) { h = T ? nb : 1 - ny; w = h * ar; if (L) nx = nr - w; if (T) ny = nb - h; }
      }
      x = nx; y = ny;
    }
    st.crop = {x, y, w, h}; paintBox();
  });
  const end = () => { drag = null; };
  box.addEventListener("pointerup", end); box.addEventListener("pointercancel", end);
  const onResize = () => drawBase();
  window.addEventListener("resize", onResize);

  const result = new Promise((resolve) => {
    cancel.addEventListener("click", () => { d.close(); resolve(null); });
    d.addEventListener("cancel", () => resolve(null));
    done.addEventListener("click", async () => {
      done.disabled = true;
      const W = rw(), H = rh(), c = st.crop;
      const full = document.createElement("canvas");
      full.width = Math.max(1, Math.round(c.w * W)); full.height = Math.max(1, Math.round(c.h * H));
      const x = full.getContext("2d");
      x.translate(-c.x * W, -c.y * H);
      x.translate(W / 2, H / 2); x.rotate(st.rot * Math.PI / 2);
      x.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
      x.setTransform(1, 0, 0, 1, 0, 0);
      if (st.b !== 100 || st.c !== 100 || st.s !== 100) {
        const px = x.getImageData(0, 0, full.width, full.height);   // pixel math: ctx.filter is missing in Safari
        adjustPixels(px.data, st.b / 100, st.c / 100, st.s / 100);
        x.putImageData(px, 0, 0);
      }
      if (st.v) vignette(x, full.width, full.height, st.v / 100);   // 26.10.13: baked in, same drawing as the preview
      const png = /png/i.test(file.type);
      const blob = await toBlob(full, png ? "image/png" : "image/jpeg", 0.92);
      full.width = full.height = 0;
      d.close();
      resolve(blob ? new File([blob], (file.name || "photo").replace(/\.[^.]+$/, "") + (png ? ".png" : ".jpg"), {type: blob.type}) : null);
    });
  });
  d.showModal();
  requestAnimationFrame(drawBase);
  const out = await result;
  window.removeEventListener("resize", onResize);
  URL.revokeObjectURL(url);
  return out;
}

/* ---------- video editor ---------- */
/* Resolves to {start, end, mute, dur} (seconds) or null if cancelled. The cut itself happens at send time
   (vcompress.js with WebCodecs, else recordEdit below). */
export function editVideo(url, prev) {
  const d = dialog("Edit video");
  const v = el("video", {class: "medit-video", src: url, playsinline: true, controls: true, preload: "auto"});
  v.muted = !!(prev && prev.mute);
  const a = el("input", {type: "range", min: "0", max: "1000", value: "0", "aria-label": "Start", "data-trim": "start"});
  const b = el("input", {type: "range", min: "0", max: "1000", value: "1000", "aria-label": "End", "data-trim": "end"});
  const lab = el("p", {class: "medit-times", "aria-live": "polite"});
  const mute = el("input", {type: "checkbox", "data-act": "mute"});
  mute.checked = !!(prev && prev.mute);
  const done = el("button", {type: "button", class: "solid", "data-act": "done"}, "Done");
  const cancel = el("button", {type: "button", class: "ghost", "data-act": "cancel"}, "Cancel");
  d.append(el("h2", {}, "Edit video"), v,
    el("div", {class: "medit-trim"}, el("label", {}, el("span", {}, "Start"), a), el("label", {}, el("span", {}, "End"), b)), lab,
    el("label", {class: "medit-check"}, mute, el("span", {}, "Mute sound")), el("div", {class: "actions"}, cancel, done));
  const fmt = (t) => Math.floor(t / 60) + ":" + (t % 60).toFixed(1).padStart(4, "0");
  let dur = 0;
  const S = () => (a.value / 1000) * dur, E = () => (b.value / 1000) * dur;
  const paint = () => { lab.textContent = dur ? `${fmt(S())} \u2013 ${fmt(E())} (${(E() - S()).toFixed(1)} s)` : "Loading\u2026"; };
  v.addEventListener("loadedmetadata", () => {
    dur = isFinite(v.duration) ? v.duration : 0;
    if (prev && dur) { a.value = Math.round(prev.start / dur * 1000); b.value = Math.round(prev.end / dur * 1000); }
    paint();
  });
  const gap = 5;   // keep at least 0.5% between the handles
  a.addEventListener("input", () => { if (+a.value > +b.value - gap) a.value = +b.value - gap; v.currentTime = S(); paint(); });
  b.addEventListener("input", () => { if (+b.value < +a.value + gap) b.value = +a.value + gap; v.currentTime = E(); paint(); });
  v.addEventListener("timeupdate", () => { if (dur && v.currentTime > E()) { v.pause(); v.currentTime = S(); } });
  v.addEventListener("play", () => { if (dur && (v.currentTime < S() - 0.05 || v.currentTime >= E())) v.currentTime = S(); });
  mute.addEventListener("change", () => { v.muted = mute.checked; });
  paint();
  return new Promise((resolve) => {
    cancel.addEventListener("click", () => { d.close(); resolve(null); });
    d.addEventListener("cancel", () => resolve(null));
    done.addEventListener("click", () => {
      v.pause(); d.close();
      const start = S(), end = E();
      const whole = start < 0.05 && end > dur - 0.05;
      resolve(whole && !mute.checked ? {start: 0, end: dur, mute: false, dur, none: true} : {start, end, mute: mute.checked, dur});
    });
    d.showModal();
  });
}

/* MediaRecorder fallback for trimming when WebCodecs can't do it: plays the cut through a canvas in real time.
   Picks whatever the browser records (Safari: mp4/H.264, Chromium/Firefox: webm). */
export function recorderType() {
  if (typeof MediaRecorder === "undefined" || !HTMLCanvasElement.prototype.captureStream) return "";
  for (const t of ["video/mp4;codecs=avc1,mp4a.40.2", "video/mp4", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"]) {
    try { if (MediaRecorder.isTypeSupported(t)) return t; } catch (e) {}
  }
  return "";
}
export async function recordEdit(blob, edit, quality, onProgress, signal) {
  const type = recorderType();
  if (!type) throw new Error("This browser can't trim videos. Send it without trimming, or try another browser.");
  const url = URL.createObjectURL(blob);
  const v = document.createElement("video");
  v.src = url; v.playsInline = true; v.muted = true; v.preload = "auto";
  try {
    await new Promise((ok, bad) => { v.onloadedmetadata = ok; v.onerror = () => bad(new Error("That video could not be opened.")); });
    const lim = quality === "sd" ? [1280, 720] : quality === "hd" ? [1920, 1080] : [1e5, 1e5];
    const W0 = v.videoWidth, H0 = v.videoHeight;
    const k = Math.min(1, lim[0] / Math.max(W0, H0), lim[1] / Math.min(W0, H0));
    const c = document.createElement("canvas");
    c.width = Math.round(W0 * k / 2) * 2; c.height = Math.round(H0 * k / 2) * 2;
    const x = c.getContext("2d");
    const stream = c.captureStream(30);
    let actx = null;
    if (!edit.mute) {
      try {
        actx = new (window.AudioContext || window.webkitAudioContext)();
        const src = actx.createMediaElementSource(v), dst = actx.createMediaStreamDestination();
        src.connect(dst); v.muted = false;
        for (const t of dst.stream.getAudioTracks()) stream.addTrack(t);
      } catch (e) { actx = null; }
    }
    const bps = quality === "sd" ? 2500000 : quality === "hd" ? 5000000 : 10000000;
    const rec = new MediaRecorder(stream, {mimeType: type, videoBitsPerSecond: bps});
    const parts = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) parts.push(e.data); };
    v.currentTime = edit.start;
    await new Promise((r) => { v.onseeked = r; });
    const stopped = new Promise((r) => { rec.onstop = r; });
    rec.start(500);
    await v.play();
    await new Promise((ok) => {
      const tick = () => {
        if (signal && signal.aborted) { ok(); return; }
        x.drawImage(v, 0, 0, c.width, c.height);
        onProgress && onProgress(Math.min(99, Math.round((v.currentTime - edit.start) / Math.max(0.1, edit.end - edit.start) * 100)));
        if (v.currentTime >= edit.end || v.ended) { ok(); return; }
        requestAnimationFrame(tick);
      };
      tick();
    });
    v.pause(); rec.stop(); await stopped;
    if (actx) actx.close();
    if (signal && signal.aborted) throw new Error("cancelled");
    const mime = type.split(";")[0];
    return new Blob(parts, {type: mime});
  } finally { URL.revokeObjectURL(url); v.removeAttribute("src"); }
}

/* ---------- zoom for the full-screen photo ---------- */
/* Pinch (two pointers), double-tap / double-click, mouse wheel / trackpad pinch (ctrl+wheel), and drag to pan.
   Exposes reset() so the viewer can go back to 1x each time it opens. */
export function attachZoom(img) {
  const z = {s: 1, x: 0, y: 0, moved: false};
  const pts = new Map();
  let start = null, lastTap = 0;
  img.style.touchAction = "none";
  const clamp = () => {
    z.s = Math.min(6, Math.max(1, z.s));
    const mx = (img.clientWidth * (z.s - 1)) / 2, my = (img.clientHeight * (z.s - 1)) / 2;
    z.x = Math.min(mx, Math.max(-mx, z.x)); z.y = Math.min(my, Math.max(-my, z.y));
  };
  const paint = () => {
    clamp();
    img.style.transform = z.s === 1 ? "" : `translate(${z.x}px, ${z.y}px) scale(${z.s})`;
    img.classList.toggle("is-zoomed", z.s > 1);
    img.dataset.zoom = z.s.toFixed(2);
  };
  // zoom toward a screen point (cx, cy)
  const zoomAt = (ns, cx, cy) => {
    const r = img.getBoundingClientRect();
    const ox = cx - (r.left + r.width / 2), oy = cy - (r.top + r.height / 2);   // offset from current centre
    const f = Math.min(6, Math.max(1, ns)) / z.s;
    z.x -= ox * (f - 1); z.y -= oy * (f - 1); z.s *= f;
    paint();
  };
  img.addEventListener("pointerdown", (e) => {
    try { img.setPointerCapture(e.pointerId); } catch (x) {}
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    z.moved = false;
    start = {s: z.s, x: z.x, y: z.y, pts: new Map([...pts].map(([k, v]) => [k, {...v}]))};
    if (pts.size === 2) { const [p, q] = [...pts.values()]; start.d = Math.hypot(p.x - q.x, p.y - q.y); start.c = {x: (p.x + q.x) / 2, y: (p.y + q.y) / 2}; }
  });
  img.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId) || !start) return;
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    if (pts.size >= 2 && start.d) {
      const [p, q] = [...pts.values()];
      const d = Math.hypot(p.x - q.x, p.y - q.y), c = {x: (p.x + q.x) / 2, y: (p.y + q.y) / 2};
      z.s = start.s; z.x = start.x + (c.x - start.c.x); z.y = start.y + (c.y - start.c.y);
      zoomAt(start.s * d / start.d, c.x, c.y);
      z.moved = true;
    } else if (pts.size === 1 && z.s > 1) {
      const s0 = start.pts.get(e.pointerId); if (!s0) return;
      const dx = e.clientX - s0.x, dy = e.clientY - s0.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) z.moved = true;
      z.x = start.x + dx; z.y = start.y + dy; paint();
    }
  });
  const up = (e) => {
    const was = pts.size;
    pts.delete(e.pointerId);
    if (was === 1 && !z.moved && e.type === "pointerup") {
      const now = Date.now();
      if (now - lastTap < 320) { lastTap = 0; if (z.s > 1) reset(); else zoomAt(2.5, e.clientX, e.clientY); z.moved = true; }
      else lastTap = now;
    }
    start = pts.size ? {s: z.s, x: z.x, y: z.y, pts: new Map([...pts].map(([k, v]) => [k, {...v}]))} : null;
  };
  img.addEventListener("pointerup", up); img.addEventListener("pointercancel", up);
  img.addEventListener("dblclick", (e) => e.preventDefault());
  img.addEventListener("wheel", (e) => {
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;   // lines -> pixels (Firefox)
    zoomAt(z.s * Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.002)), e.clientX, e.clientY);
  }, {passive: false});
  // Safari's own pinch events: stop the page zooming instead of the photo (pointer events still do the work)
  for (const t of ["gesturestart", "gesturechange"]) img.addEventListener(t, (e) => e.preventDefault());
  // a click that ended a pan/zoom must not close the viewer
  img.addEventListener("click", (e) => { if (z.moved || z.s > 1) e.stopPropagation(); }, true);
  function reset() { z.s = 1; z.x = z.y = 0; paint(); }
  return {reset, state: z, zoomAt};
}
