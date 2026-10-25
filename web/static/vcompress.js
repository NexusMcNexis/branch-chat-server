/* 26.10.11: shrink a video on the SENDER's device before it is encrypted and uploaded.
   WebCodecs decode -> up to 1080p H.264 (about 5 Mbps; 26.10.11.1, was 720p / 2 Mbps) -> mp4, with the original AAC audio copied across
   unchanged (no re-encode, so it works where AudioEncoder has no AAC). Rotation from the phone's
   track matrix is baked into the pixels. Anything this can't handle returns null and the original is sent. */

const MAX_FPS = 30;
/* 26.10.12: the sender picks the quality. hd = 1080p ~5 Mbps, sd = 720p ~2.5 Mbps, original = full size (only re-encoded
   when trimming or muting, at ~12 Mbps for 1080p-equivalent). */
export const QUALITY = {
  hd: {long: 1920, short: 1080, bps: 5000000},
  sd: {long: 1280, short: 720, bps: 2500000},
  original: {long: 1e5, short: 1e5, bps: 12000000},
};

export function canCompressVideo() {
  return typeof VideoEncoder !== "undefined" && typeof VideoDecoder !== "undefined" &&
    typeof OffscreenCanvas !== "undefined" && !!globalThis.MP4Box &&
    !!((globalThis.MP4Box && globalThis.MP4Box.DataStream) || globalThis.DataStream);
}

function why(r) { globalThis.__vcWhy = r; return null; }

function even(v) { return Math.max(2, Math.round(v / 2) * 2); }

function decoderDescription(trak) {
  const DS = (globalThis.MP4Box && globalThis.MP4Box.DataStream) || globalThis.DataStream;
  for (const entry of trak.mdia.minf.stbl.stsd.entries) {
    const box = entry.avcC || entry.hvcC || entry.vpcC || entry.av1C;
    if (!box) continue;
    const s = new DS(undefined, 0, DS.BIG_ENDIAN);
    box.write(s);
    return new Uint8Array(s.buffer, 8);
  }
  return why("r1");
}

function aacConfig(trak) {
  for (const entry of trak.mdia.minf.stbl.stsd.entries) {
    try {
      const dsi = entry.esds.esd.descs[0].descs[0];
      if (dsi && dsi.data) return new Uint8Array(dsi.data);
    } catch (e) { /* not AAC */ }
  }
  return why("r2");
}

/* iPhone .mov files keep the AAC setup inside a "wave" box mp4box doesn't open; rebuild the plain AAC-LC one. */
function aacFallback(rate, ch) {
  const rates = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350];
  const fi = rates.indexOf(rate);
  if (fi < 0 || ch < 1 || ch > 7) return null;
  return new Uint8Array([(2 << 3) | (fi >> 1), ((fi & 1) << 7) | (ch << 3)]);
}

function rotationOf(matrix) {
  if (!matrix || matrix.length < 2) return 0;
  const deg = Math.round(Math.atan2(matrix[1], matrix[0]) * 180 / Math.PI);
  return ((deg % 360) + 360) % 360;
}

function demux(buffer) {
  const MP4Box = globalThis.MP4Box;
  return new Promise((resolve, reject) => {
    const file = MP4Box.createFile();
    const samples = new Map();
    let info = null;
    file.onError = (e) => reject(new Error(String(e || "mp4")));
    file.onReady = (i) => {
      info = i;
      for (const t of i.tracks) { samples.set(t.id, []); file.setExtractionOptions(t.id, null, {nbSamples: 1e9}); }
      file.start();
    };
    file.onSamples = (id, _u, list) => { const arr = samples.get(id); for (const s of list) arr.push(s); };
    buffer.fileStart = 0;
    try { file.appendBuffer(buffer); file.flush(); } catch (e) { reject(e); return; }
    if (!info) { reject(new Error("not an mp4/mov")); return; }
    resolve({file, info, samples});
  });
}

/* Should we bother? Small or already-lean videos are sent as they are. */
export function worthCompressing(size, info, q) {
  q = q || QUALITY.hd;
  if (size < (q === QUALITY.sd ? 3 : 6) * 1024 * 1024) return false;
  const v = info && info.videoTracks && info.videoTracks[0];
  if (!v) return false;
  const secs = (info.duration || 0) / (info.timescale || 1);
  const bps = secs > 0 ? size * 8 / secs : Infinity;
  const long = Math.max(v.video.width, v.video.height);
  return !(long <= q.long && bps < q.bps * 1.3);
}

/* Returns {blob, before, after, ms} or null (send the original). Throws Error("cancelled") on abort. */
/* opts: {quality: "hd"|"sd"|"original", trim: {start, end} seconds, mute: bool}. An edit (trim/mute) always re-encodes. */
export async function compressVideo(blob, onProgress, signal, opts) {
  opts = opts || {};
  const q = QUALITY[opts.quality] || QUALITY.hd;
  const tr = opts.trim || null;
  const edited = !!(tr || opts.mute);
  const TARGET_LONG = q.long, TARGET_SHORT = q.short;
  if (!canCompressVideo()) return why("r3");
  const t0 = performance.now();
  const buffer = await blob.arrayBuffer();
  let d;
  try { d = await demux(buffer); } catch (e) { return why("r4"); }
  const {file, info, samples} = d;
  if (!edited && !worthCompressing(blob.size, info, q)) return why("r5");
  const vt = info.videoTracks[0];
  const at = opts.mute ? null : (info.audioTracks && info.audioTracks[0]);
  const vtrak = file.getTrackById(vt.id);
  const description = decoderDescription(vtrak);
  if (!description) return why("r6");
  let audio = null;
  if (at) {
    if (!/^mp4a/.test(at.codec)) return why("r7");           // Opus/other audio: don't risk losing the sound
    const cfg = aacConfig(file.getTrackById(at.id)) || aacFallback(at.audio.sample_rate, at.audio.channel_count);
    if (!cfg) return why("r8");
    audio = {cfg, rate: at.audio.sample_rate, ch: at.audio.channel_count, codec: at.codec, ts: at.timescale};
  }
  const rot = rotationOf(vt.matrix);
  const srcW = vt.video.width, srcH = vt.video.height;
  const turned = rot === 90 || rot === 270;
  const dispW = turned ? srcH : srcW, dispH = turned ? srcW : srcH;
  const scale = Math.min(1, TARGET_LONG / Math.max(dispW, dispH), TARGET_SHORT / Math.min(dispW, dispH));
  const outW = even(dispW * scale), outH = even(dispH * scale);
  const bitrate = Math.max(800000, Math.round(q.bps * (outW * outH) / (q === QUALITY.original ? 1920 * 1080 : q.long * q.short)));

  const decCfg = {codec: vt.codec, codedWidth: srcW, codedHeight: srcH, description};
  try { if (!(await VideoDecoder.isConfigSupported(decCfg)).supported) return why("r9"); } catch (e) { return why("r10"); }
  let encCfg = null;
  for (const codec of ["avc1.4D4028", "avc1.640028", "avc1.42E028", "avc1.4D401F", "avc1.42E01F"]) {   // level 4.0 first (1080p needs it)
    const c = {codec, width: outW, height: outH, bitrate, framerate: MAX_FPS, avc: {format: "avc"}};
    try { if ((await VideoEncoder.isConfigSupported(c)).supported) { encCfg = c; break; } } catch (e) { /* next */ }
  }
  if (!encCfg) return why("r11");

  const {Muxer, ArrayBufferTarget} = await import("/vendor/mp4-muxer.mjs");
  const target = new ArrayBufferTarget();
  const muxer = new Muxer({
    target, fastStart: "in-memory", firstTimestampBehavior: "offset",
    video: {codec: "avc", width: outW, height: outH, frameRate: MAX_FPS},
    audio: audio ? {codec: "aac", numberOfChannels: audio.ch, sampleRate: audio.rate} : undefined,
  });
  const aList = audio ? samples.get(at.id) : [];
  const aMeta = audio ? {decoderConfig: {codec: audio.codec, sampleRate: audio.rate, numberOfChannels: audio.ch, description: audio.cfg}} : null;
  let aNext = 0, aSkipped = 0;
  const t0us = tr ? Math.round(tr.start * 1e6) : 0, t1us = tr ? Math.round(tr.end * 1e6) : Infinity;
  const pushAudio = (upTo) => {
    while (aNext < aList.length) {
      const s = aList[aNext];
      const ts = Math.round(s.cts * 1e6 / s.timescale) - t0us;
      if (ts > upTo) break;
      if (ts < 0 || ts > t1us - t0us) { aNext += 1; aSkipped += 1; continue; }
      muxer.addAudioChunkRaw(new Uint8Array(s.data), "key", ts, Math.round(s.duration * 1e6 / s.timescale), aNext - aSkipped === 0 ? aMeta : undefined);
      aNext += 1;
    }
  };
  let failed = null;
  const step = 1e6 / MAX_FPS;
  const fed = [];
  let outN = 0;
  const encoder = new VideoEncoder({output: (c, m) => {
    // Some encoders (Firefox) emit B-frames: chunks arrive in decode order with presentation times out of
    // order. The n-th chunk decodes at the n-th frame time we fed in; everything shows one frame late so
    // the composition offset is never negative.
    // Audio is woven in as we go (Firefox won't play an mp4 whose audio all sits after the video).
    try { const dts = fed[outN++]; pushAudio(dts + step); muxer.addVideoChunk(c, m, c.timestamp + step, c.timestamp + step - dts); } catch (e) { failed = e; }
  }, error: (e) => { failed = e; }});
  encoder.configure(encCfg);
  const canvas = new OffscreenCanvas(outW, outH);
  const ctx = canvas.getContext("2d", {alpha: false});
  const vs = samples.get(vt.id);
  let nextTs = -Infinity, encoded = 0, done = 0, lastKey = -Infinity;
  const decoder = new VideoDecoder({
    output: (frame) => {
      done += 1;
      // Snap to a 30 fps grid and keep it strictly increasing (60 fps phones drop every other frame;
      // a decoder that hands frames back out of order can't make the encoder's timestamps go backwards).
      const ts = Math.round((frame.timestamp - t0us) / step) * step;
      if (failed || signal.aborted || ts < nextTs || ts < 0 || frame.timestamp > t1us) { frame.close(); return; }
      nextTs = ts + step;
      ctx.save();
      ctx.translate(outW / 2, outH / 2);
      ctx.rotate(rot * Math.PI / 180);
      const w = turned ? outH : outW, h = turned ? outW : outH;
      ctx.drawImage(frame, -w / 2, -h / 2, w, h);
      ctx.restore();
      frame.close();
      const out = new VideoFrame(canvas, {timestamp: ts, duration: step});
      const key = ts - lastKey >= 2e6;
      if (key) lastKey = ts;
      fed.push(ts);
      encoder.encode(out, {keyFrame: key});
      out.close();
      encoded += 1;
      onProgress(Math.min(99, Math.round(done / vs.length * 100)));
    },
    error: (e) => { failed = e; },
  });
  decoder.configure(decCfg);
  const cleanup = () => { try { decoder.close(); } catch (e) {} try { encoder.close(); } catch (e) {} };
  try {
    for (const s of vs) {
      if (signal.aborted) throw new Error("cancelled");
      if (failed) throw failed;
      if (s.cts * 1e6 / s.timescale > t1us + 1e6 && s.is_sync) break;   // past the trimmed end
      decoder.decode(new EncodedVideoChunk({
        type: s.is_sync ? "key" : "delta",
        timestamp: Math.round(s.cts * 1e6 / s.timescale),
        duration: Math.round(s.duration * 1e6 / s.timescale),
        data: s.data,
      }));
      while ((decoder.decodeQueueSize > 6 || encoder.encodeQueueSize > 6) && !failed && !signal.aborted) {
        await new Promise((r) => setTimeout(r, 4));
      }
    }
    await decoder.flush();
    await encoder.flush();
    if (signal.aborted) throw new Error("cancelled");
    if (failed || !encoded) return why("r12");
    pushAudio(Infinity);
    muxer.finalize();
  } catch (e) {
    cleanup();
    globalThis.__vcErr = String(e && (e.message || e));
    if (signal.aborted || (e && e.message === "cancelled")) throw new Error("cancelled");
    return why("r13");
  }
  cleanup();
  const out = new Blob([target.buffer], {type: "video/mp4"});
  onProgress(100);
  if (!edited && out.size > blob.size * 0.85) return why("r14");   // didn't help enough: keep the original quality
  return {blob: out, before: blob.size, after: out.size, ms: Math.round(performance.now() - t0), width: outW, height: outH};
}
