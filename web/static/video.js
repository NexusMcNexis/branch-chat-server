/* Prepare a decrypted video when this browser cannot play it.
   WebCodecs is tried first. Firefox Android uses the single-thread
   ffmpeg build, which does not need shared memory. */

let ffmpegPromise = null;

function even(value) {
  return Math.max(2, Math.floor(value / 2) * 2);
}

function targetSize(width, height) {
  const scale = Math.min(1, 1920 / width, 1080 / height);
  return {width: even(width * scale), height: even(height * scale)};
}

function descriptionFrom(track) {
  const MP4Box = globalThis.MP4Box;
  if (!MP4Box || !MP4Box.DataStream || !track) return null;
  const entries = track.mdia && track.mdia.minf && track.mdia.minf.stbl && track.mdia.minf.stbl.stsd && track.mdia.minf.stbl.stsd.entries;
  if (!entries) return null;
  for (const entry of entries) {
    const box = entry.avcC || entry.hvcC || entry.vpcC || entry.av1C;
    if (!box || typeof box.write !== "function") continue;
    const stream = new MP4Box.DataStream(undefined, 0, MP4Box.DataStream.BIG_ENDIAN);
    box.write(stream);
    return new Uint8Array(stream.buffer).slice(8);
  }
  return null;
}

async function webCodecs(bytes, onProgress, signal) {
  const MP4Box = globalThis.MP4Box;
  if (!MP4Box || typeof MP4Box.createFile !== "function") throw new Error("no mp4box");
  if (typeof VideoEncoder === "undefined" || typeof OffscreenCanvas === "undefined") throw new Error("no webcodecs");
  const {Muxer, ArrayBufferTarget} = await import("/vendor/mp4-muxer.mjs");
  const file = MP4Box.createFile(true);
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  buffer.fileStart = 0;
  let decoderError = null;
  let encoderError = null;
  let seen = 0;
  let expected = 1;
  const step = 1000000 / 30;
  let nextIn = 0;
  let outTs = 0;
  let encoder = null;
  let muxer = null;
  let target = null;
  const decoder = new VideoDecoder({
    output: (frame) => {
      if (!encoder || encoderError || signal.aborted) {
        frame.close();
        return;
      }
      if (nextIn && frame.timestamp < nextIn - step / 2) {
        frame.close();
        return;
      }
      nextIn = frame.timestamp + step;
      const canvas = encoder.canvas;
      canvas.getContext("2d", {alpha: false}).drawImage(frame, 0, 0, canvas.width, canvas.height);
      frame.close();
      const out = new VideoFrame(canvas, {timestamp: outTs, duration: step});
      encoder.encode(out, {keyFrame: seen % 60 === 0});
      out.close();
      outTs += step;
      seen += 1;
      onProgress(Math.min(99, Math.round((seen / expected) * 100)));
    },
    error: (err) => { decoderError = err || new Error("decode"); },
  });
  const ready = new Promise((resolve, reject) => {
    file.onError = (err) => reject(new Error(String(err || "mp4")));
    file.onReady = (value) => {
      try {
        const video = value.videoTracks && value.videoTracks[0];
        if (!video) throw new Error("no video");
        if (value.audioTracks && value.audioTracks.length) throw new Error("audio uses the other prepare path");
        const description = descriptionFrom(file.getTrackById(video.id));
        if (!description) throw new Error("no description");
        const size = targetSize(video.video.width, video.video.height);
        expected = Math.max(1, Math.round((video.nb_samples || 1) * 30 / (video.timescale ? (video.timescale / (video.duration || video.timescale)) : 30)));
        target = new ArrayBufferTarget();
        muxer = new Muxer({
          target,
          fastStart: "in-memory",
          firstTimestampBehavior: "offset",
          video: {codec: "avc", width: size.width, height: size.height, frameRate: 30},
        });
        encoder = new VideoEncoder({
          output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
          error: (err) => { encoderError = err || new Error("encode"); },
        });
        encoder.canvas = new OffscreenCanvas(size.width, size.height);
        const bitrate = Math.max(400000, Math.round(2500000 * (size.width * size.height) / (1920 * 1080)));
        let configured = false;
        for (const codec of ["avc1.4D0028", "avc1.4D401F", "avc1.42E01F"]) {
          try {
            encoder.configure({
              codec, width: size.width, height: size.height, bitrate, framerate: 30,
              avc: {format: "avc"}, latencyMode: "quality",
            });
            configured = true;
            break;
          } catch (err) {
            configured = false;
          }
        }
        if (!configured) throw new Error("encoder");
        decoder.configure({
          codec: video.codec,
          description,
          codedWidth: video.video.width,
          codedHeight: video.video.height,
        });
        file.setExtractionOptions(video.id, null, {nbSamples: 1000});
        file.start();
        resolve(video);
      } catch (err) {
        reject(err);
      }
    };
    file.onSamples = (_id, _user, samples) => {
      for (const sample of samples) {
        const data = sample.data instanceof ArrayBuffer ? new Uint8Array(sample.data) : new Uint8Array(sample.data);
        decoder.decode(new EncodedVideoChunk({
          type: sample.is_sync ? "key" : "delta",
          timestamp: Math.round((sample.cts * 1000000) / sample.timescale),
          duration: Math.round((sample.duration * 1000000) / sample.timescale),
          data,
        }));
      }
    };
  });
  file.appendBuffer(buffer);
  file.flush();
  await ready;
  if (decoderError) throw decoderError;
  if (signal.aborted) throw new Error("cancelled");
  await decoder.flush();
  if (decoderError || encoderError) throw decoderError || encoderError;
  if (!seen) throw new Error("no frames");
  await encoder.flush();
  if (encoderError) throw encoderError;
  muxer.finalize();
  onProgress(100);
  return new Uint8Array(target.buffer);
}

async function loadFfmpeg() {
  if (!ffmpegPromise) {
    const pending = (async () => {
      const {FFmpeg} = await import("/vendor/ffmpeg/index.js");
      const ffmpeg = new FFmpeg();
      await ffmpeg.load({
        coreURL: "/vendor/core/ffmpeg-core.js",
        wasmURL: "/vendor/core/ffmpeg-core.wasm",
      });
      return ffmpeg;
    })();
    ffmpegPromise = pending.catch((err) => {
      ffmpegPromise = null;
      throw err;
    });
  }
  return ffmpegPromise;
}

async function ffmpegTranscode(bytes, onProgress, signal) {
  const ffmpeg = await loadFfmpeg();
  const onProg = ({progress}) => {
    if (typeof progress === "number" && progress >= 0) onProgress(Math.min(99, Math.round(progress * 100)));
  };
  ffmpeg.on("progress", onProg);
  const stop = () => {
    ffmpegPromise = null;
    try { ffmpeg.terminate(); } catch (err) { /* already stopped */ }
  };
  if (signal.aborted) {
    stop();
    throw new Error("cancelled");
  }
  signal.addEventListener("abort", stop, {once: true});
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  try {
    await ffmpeg.writeFile("input.bin", copy);
    const code = await ffmpeg.exec([
      "-i", "input.bin",
      "-map", "0:v:0",
      "-map", "0:a:0?",
      "-c:v", "libx264",
      "-preset", "veryfast",
      "-crf", "23",
      "-profile:v", "main",
      "-level", "4.0",
      "-pix_fmt", "yuv420p",
      "-vf", "fps=30,scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2",
      "-c:a", "aac",
      "-b:a", "128k",
      "-ac", "2",
      "-ar", "48000",
      "-movflags", "+faststart",
      "output.mp4",
    ], 12 * 60 * 1000);
    if (code !== 0) throw new Error("prepare failed");
    const out = await ffmpeg.readFile("output.mp4");
    try { await ffmpeg.deleteFile("input.bin"); } catch (err) { /* already gone */ }
    try { await ffmpeg.deleteFile("output.mp4"); } catch (err) { /* already gone */ }
    onProgress(100);
    return out instanceof Uint8Array ? out : new Uint8Array(out);
  } finally {
    signal.removeEventListener("abort", stop);
    ffmpeg.off("progress", onProg);
  }
}

export async function transcodeVideo(bytes, onProgress, signal) {
  if (typeof VideoEncoder !== "undefined" && globalThis.MP4Box) {
    try {
      const fast = await webCodecs(bytes, onProgress, signal);
      if (fast && fast.byteLength > 32) return fast;
    } catch (err) {
      if (signal.aborted) throw err;
    }
  }
  const out = await ffmpegTranscode(bytes, onProgress, signal);
  if (!out || out.byteLength < 32) throw new Error("prepare failed");
  return out;
}
