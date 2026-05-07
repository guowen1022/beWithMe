"use client";

// SpeakerSink — listens for `voice-play` SSE events and plays them through
// Web Audio. Mounts once at the root layout so voice works on every page,
// not just the canvas.
//
// Browsers gate AudioContext until the first user gesture. We don't try
// to bypass that: the context is created on demand, and the very first
// document interaction is used to call `resume()`. Any voice-play that
// arrives before that goes into a small queue and plays once the context
// is unlocked.
//
// Audio path: voice-play SSE event → fetch /api/speak/stream with the
// requested text/voice → read raw little-endian 16-bit mono PCM chunks
// → decode each chunk into an AudioBuffer → schedule for playback in
// order. Sentence-sized chunks come fast enough that perceived latency
// stays small.

import { useEffect, useRef } from "react";

import { speakTextStream, subscribeToDynamicStream } from "@/lib/api";

interface VoiceJob {
  text: string;
  voice?: string | null;
  speed?: number | null;
  lang?: string | null;
}

function pcm16ToFloat32(bytes: Uint8Array): Float32Array {
  // Little-endian int16 → float32 in [-1, 1]. Bytes can have an odd length
  // if a chunk boundary lands mid-sample; trim down to an even count.
  // Allocate a fresh ArrayBuffer-backed Float32Array so AudioBuffer's
  // copyToChannel signature (Float32Array<ArrayBuffer>) is satisfied —
  // some envs widen Uint8Array.buffer to ArrayBufferLike, which doesn't
  // assign to ArrayBuffer.
  const evenLen = bytes.byteLength - (bytes.byteLength % 2);
  const out = new Float32Array(evenLen / 2);
  for (let i = 0; i < out.length; i++) {
    const lo = bytes[i * 2];
    const hi = bytes[i * 2 + 1];
    const u16 = (hi << 8) | lo;
    const s16 = u16 < 0x8000 ? u16 : u16 - 0x10000;
    out[i] = s16 / 32768;
  }
  return out;
}

export default function SpeakerSink() {
  // Held across renders. Recreated only on unmount.
  const ctxRef = useRef<AudioContext | null>(null);
  const nextStartRef = useRef<number>(0);
  const queueRef = useRef<VoiceJob[]>([]);
  const drainingRef = useRef<boolean>(false);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const getCtx = (): AudioContext | null => {
      if (ctxRef.current) return ctxRef.current;
      const Ctor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      ctxRef.current = new Ctor();
      return ctxRef.current;
    };

    // Unlock the audio context on the first user gesture. Browsers
    // require this for autoplay policies.
    const unlock = () => {
      const ctx = getCtx();
      if (ctx && ctx.state === "suspended") {
        ctx.resume().catch(() => {});
      }
      drainQueue();
    };
    const unlockOpts: AddEventListenerOptions = { once: false, passive: true };
    document.addEventListener("pointerdown", unlock, unlockOpts);
    document.addEventListener("keydown", unlock, unlockOpts);

    const playJob = async (job: VoiceJob) => {
      const ctx = getCtx();
      if (!ctx) return;
      const opts: { voice?: string; speed?: number; lang?: string } = {};
      if (job.voice) opts.voice = job.voice;
      if (typeof job.speed === "number") opts.speed = job.speed;
      if (job.lang) opts.lang = job.lang;

      let stream: { sampleRate: number; reader: ReadableStreamDefaultReader<Uint8Array> };
      try {
        stream = await speakTextStream(job.text, opts);
      } catch (err) {
        console.warn("[speaker-sink] /speak/stream failed", err);
        return;
      }
      const sampleRate = stream.sampleRate;

      // Schedule playback strictly after the previous chunk so utterances
      // don't overlap.
      if (nextStartRef.current < ctx.currentTime) {
        nextStartRef.current = ctx.currentTime;
      }

      // Carry over any leftover odd byte across chunks so we don't drop
      // half a sample at chunk boundaries.
      let leftover: Uint8Array | null = null;
      while (true) {
        const { value, done } = await stream.reader.read();
        if (done) break;
        if (!value || value.byteLength === 0) continue;
        let bytes: Uint8Array = value;
        if (leftover) {
          const merged = new Uint8Array(leftover.byteLength + bytes.byteLength);
          merged.set(leftover, 0);
          merged.set(bytes, leftover.byteLength);
          bytes = merged;
          leftover = null;
        }
        if (bytes.byteLength % 2 === 1) {
          leftover = bytes.slice(bytes.byteLength - 1);
          bytes = bytes.slice(0, bytes.byteLength - 1);
        }
        if (bytes.byteLength === 0) continue;
        const samples = pcm16ToFloat32(bytes);
        const buf = ctx.createBuffer(1, samples.length, sampleRate);
        // getChannelData avoids the Float32Array<ArrayBufferLike> vs
        // <ArrayBuffer> type narrowing that copyToChannel insists on.
        buf.getChannelData(0).set(samples);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        const start = Math.max(ctx.currentTime, nextStartRef.current);
        src.start(start);
        nextStartRef.current = start + buf.duration;
      }
    };

    const drainQueue = async () => {
      if (drainingRef.current) return;
      const ctx = getCtx();
      if (!ctx || ctx.state === "suspended") return;
      drainingRef.current = true;
      try {
        while (queueRef.current.length > 0) {
          const job = queueRef.current.shift()!;
          await playJob(job);
        }
      } finally {
        drainingRef.current = false;
      }
    };

    const ctrl = new AbortController();
    subscribeToDynamicStream((event) => {
      if (event.type !== "voice-play") return;
      queueRef.current.push({
        text: event.text,
        voice: event.voice,
        speed: event.speed,
        lang: event.lang,
      });
      drainQueue();
    }, ctrl.signal).catch((err) => {
      if (err?.name !== "AbortError") {
        console.warn("[speaker-sink] stream ended", err);
      }
    });

    return () => {
      ctrl.abort();
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
      const ctx = ctxRef.current;
      if (ctx) {
        ctx.close().catch(() => {});
        ctxRef.current = null;
      }
    };
  }, []);

  return null;
}
