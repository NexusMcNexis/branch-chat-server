/* profile-crop (26.10.13.x): round profile-picture crop. Drag to move, pinch / wheel / slider to zoom, Reset.
   "Edit" opens the SAME photo editor used for shared images (mediaedit.js editPhoto: rotate, crop, brightness,
   contrast, saturation, vignette); its result comes back here for the round crop. Output: 512px JPEG under the
   server's 150 KB avatar limit. Look: "Canopy" (leafy tint, dashed leaf-green guide). */
import {editPhoto} from "./mediaedit.js";

const OUT = 512, MAX_BYTES = 140 * 1024;

/* Pure crop math (drafted by qwen3:30b-a3b, reviewed and corrected). st = {iw, ih, D, zoom, ox, oy}. */
export function cropClamp(st) {
  st.zoom = Math.min(8, Math.max(1, st.zoom));
  const s = st.D / Math.min(st.iw, st.ih) * st.zoom;
  const mx = Math.max(0, (st.iw * s - st.D) / 2), my = Math.max(0, (st.ih * s - st.D) / 2);
  st.ox = Math.min(mx, Math.max(-mx, st.ox)); st.oy = Math.min(my, Math.max(-my, st.oy));
  return st;
}
export function cropZoom(st, factor, ax = 0, ay = 0) {
  const z0 = st.zoom; st.zoom = Math.min(8, Math.max(1, z0 * factor));
  const k = st.zoom / z0;   // the image point under (ax, ay) stays put
  st.ox = ax - (ax - st.ox) * k; st.oy = ay - (ay - st.oy) * k;
  return cropClamp(st);
}
export function cropRect(st) {
  const s = st.D / Math.min(st.iw, st.ih) * st.zoom, sw = st.D / s;
  return {sx: st.iw / 2 - st.ox / s - sw / 2, sy: st.ih / 2 - st.oy / s - sw / 2, sw};
}

function loadImg(blob) {
  return new Promise((ok, bad) => {
    const u = URL.createObjectURL(blob), i = new Image();
    i.onload = () => ok({img: i, url: u});
    i.onerror = () => { URL.revokeObjectURL(u); bad(new Error("That picture was not read.")); };
    i.src = u;
  });
}
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v != null) e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
async function encode(img, r) {
  const c = document.createElement("canvas"); c.width = c.height = OUT;
  const x = c.getContext("2d");
  x.fillStyle = "#f5c518"; x.fillRect(0, 0, OUT, OUT);   // same fill as before under transparent PNGs
  x.imageSmoothingQuality = "high";
  x.drawImage(img, r.sx, r.sy, r.sw, r.sw, 0, 0, OUT, OUT);
  let blob = null;
  for (const q of [0.9, 0.84, 0.76, 0.66, 0.55]) {
    blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", q));
    if (blob && blob.size <= MAX_BYTES) break;
  }
  c.width = c.height = 0;
  if (!blob) throw new Error("That picture was not read.");
  return blob;
}

/* Resolves to a 512px JPEG Blob, or null if cancelled. */
export async function cropAvatar(file) {
  let L = await loadImg(file), src = file;
  const st = {iw: L.img.naturalWidth, ih: L.img.naturalHeight, D: 280, zoom: 1, ox: 0, oy: 0};
  const d = h("dialog", {class: "avc avc-canopy", "aria-label": "Crop profile picture"});
  const img = h("img", {class: "avc-img", alt: "", draggable: "false"});
  const stage = h("div", {class: "avc-stage", "data-testid": "avatar-crop-stage", tabindex: "0", "aria-label": "Drag to move, pinch or scroll to zoom"}, img, h("div", {class: "avc-ring", "aria-hidden": "true"}));
  const prev = h("canvas", {class: "avc-preview", width: "128", height: "128", "aria-label": "Preview"});
  const prevS = h("canvas", {class: "avc-preview avc-preview-s", width: "64", height: "64", "aria-hidden": "true"});
  const zoom = h("input", {type: "range", min: "100", max: "800", step: "1", value: "100", "aria-label": "Zoom", "data-testid": "avatar-zoom"});
  const btn = (cls, txt, fn, extra) => h("button", Object.assign({type: "button", class: cls, onclick: fn}, extra || {}), txt);
  const cancel = btn("ghost", "Cancel", null, {"data-act": "cancel"});
  const done = btn("solid", "Use picture", null, {"data-act": "done"});
  d.append(
    h("h2", {}, "Your profile picture"),
    h("div", {class: "avc-main"}, stage, h("div", {class: "avc-side"}, prev, prevS, h("span", {class: "avc-hint"}, "How friends see you"))),
    h("label", {class: "avc-zoom"}, h("span", {"aria-hidden": "true"}, "\u2212"), zoom, h("span", {"aria-hidden": "true"}, "+")),
    h("div", {class: "avc-tools"},
      btn("ghost", "\u270E Edit photo", () => openEditor(), {"data-act": "edit"}),
      btn("ghost", "Reset", () => { st.zoom = 1; st.ox = st.oy = 0; paint(); }, {"data-act": "reset"})),
    h("div", {class: "actions"}, cancel, done));
  document.body.append(d);

  let raf = 0;
  function paint() {
    cropClamp(st);
    const s = st.D / Math.min(st.iw, st.ih) * st.zoom;
    img.style.width = st.iw * s + "px"; img.style.height = st.ih * s + "px";
    img.style.transform = `translate(-50%, -50%) translate(${st.ox}px, ${st.oy}px)`;
    zoom.value = String(Math.round(st.zoom * 100));
    if (!raf) raf = requestAnimationFrame(() => {
      raf = 0; const r = cropRect(st);
      for (const c of [prev, prevS]) { const x = c.getContext("2d"); x.clearRect(0, 0, c.width, c.height); x.drawImage(L.img, r.sx, r.sy, r.sw, r.sw, 0, 0, c.width, c.height); }
    });
  }
  function measure() { const D = stage.clientWidth; if (D > 0) { const k = D / st.D; st.ox *= k; st.oy *= k; st.D = D; } paint(); }
  async function openEditor() {
    d.close("edit");
    try {
      const out = await editPhoto(src);
      if (out) { const N = await loadImg(out); URL.revokeObjectURL(L.url); L = N; src = out; img.src = L.url; st.iw = L.img.naturalWidth; st.ih = L.img.naturalHeight; st.zoom = 1; st.ox = st.oy = 0; }
    } catch (e) {}
    d.showModal(); requestAnimationFrame(measure);
  }

  // pointers: one finger / mouse drags, two fingers pinch (zoom about the midpoint)
  const pts = new Map(); let pinch = 0;
  const local = (e) => { const b = stage.getBoundingClientRect(); return {x: e.clientX - b.left - b.width / 2, y: e.clientY - b.top - b.height / 2}; };
  stage.addEventListener("pointerdown", (e) => { e.preventDefault(); try { stage.setPointerCapture(e.pointerId); } catch (x) {} pts.set(e.pointerId, local(e)); pinch = 0; });
  stage.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    const p = local(e), o = pts.get(e.pointerId);
    if (pts.size === 1) { st.ox += p.x - o.x; st.oy += p.y - o.y; pts.set(e.pointerId, p); paint(); return; }
    pts.set(e.pointerId, p);
    const [a, b] = [...pts.values()], dist = Math.hypot(a.x - b.x, a.y - b.y);
    if (pinch && dist) cropZoom(st, dist / pinch, (a.x + b.x) / 2, (a.y + b.y) / 2);
    pinch = dist; paint();
  });
  const up = (e) => { pts.delete(e.pointerId); pinch = 0; };
  stage.addEventListener("pointerup", up); stage.addEventListener("pointercancel", up);
  stage.addEventListener("wheel", (e) => { e.preventDefault(); const p = local(e); cropZoom(st, Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0015)), p.x, p.y); paint(); }, {passive: false});
  stage.addEventListener("keydown", (e) => {
    const m = {ArrowLeft: [8, 0], ArrowRight: [-8, 0], ArrowUp: [0, 8], ArrowDown: [0, -8]}[e.key];
    if (m) { st.ox += m[0]; st.oy += m[1]; } else if (e.key === "+" || e.key === "=") cropZoom(st, 1.1); else if (e.key === "-") cropZoom(st, 1 / 1.1); else return;
    e.preventDefault(); paint();
  });
  zoom.addEventListener("input", () => { cropZoom(st, zoom.value / 100 / st.zoom); paint(); });
  window.addEventListener("resize", measure);

  const result = new Promise((resolve) => {
    cancel.addEventListener("click", () => { d.close(); resolve(null); });
    d.addEventListener("cancel", (e) => { resolve(null); });
    d.addEventListener("close", () => { if (d.returnValue !== "edit") resolve(null); });
    done.addEventListener("click", async () => {
      done.disabled = true;
      try { const b = await encode(L.img, cropRect(st)); d.returnValue = "done"; d.close("done"); resolve(b); }
      catch (e) { done.disabled = false; }
    });
  });
  img.src = L.url;
  d.showModal(); requestAnimationFrame(measure);
  const out = await result;
  window.removeEventListener("resize", measure);
  URL.revokeObjectURL(L.url);
  if (d.open) d.close();
  setTimeout(() => d.remove(), 0);
  return out;
}
