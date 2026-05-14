// Browser VAD: wraps @ricky0123/vad-web (silero-vad ONNX) and emits both
// mid-phrase interim audio (for live captions) and phrase-final audio (for
// LLM-facing commits).
//
// Why two callbacks:
//   - `onInterim(wav, phraseId)` fires every ~500 ms of in-phrase speech so
//     the UI can show rolling captions. Each WAV grows within a phrase
//     (bounded — resets on speech end), so we avoid the old O(utterance²).
//   - `onPhrase(wav, phraseId)` fires once when silero-vad decides the
//     phrase ended. This is the clean, fully-buffered audio (incl. pre-roll)
//     that becomes the committed transcript and the unit handed to the LLM.
//
// The phraseId is bumped on every speech onset so consumers can discard
// stale interim responses that arrive after the phrase committed.

import type { MicVAD } from "@ricky0123/vad-web";

export interface MicVadHandle {
  start(): void;
  pause(): void;
  /** Force-finalize the in-flight phrase by encoding the buffered frames
   *  and firing onPhrase synchronously. No-op if not currently in speech.
   *  Caller must still pause()/destroy() afterwards to release the mic. */
  flush(): void;
  destroy(): void;
}

export interface MicVadOptions {
  onPhrase: (wav: Blob, phraseId: number) => void;
  onInterim?: (wav: Blob, phraseId: number) => void;
  onSpeechStart?: (phraseId: number) => void;
  onError?: (err: unknown) => void;
}

// Frame size in @ricky0123/vad-web is 512 samples @ 16 kHz ≈ 32 ms.
//   redemptionFrames: 20 → 640 ms of silence ends a phrase
//   minSpeechFrames:  4  → 128 ms floor; filters coughs / single clicks
//   preSpeechPadFrames: 6 → 192 ms pre-roll; rescues word-initial consonants
const VAD_TUNING = {
  positiveSpeechThreshold: 0.5,
  negativeSpeechThreshold: 0.35,
  redemptionFrames: 20,
  minSpeechFrames: 4,
  preSpeechPadFrames: 6,
} as const;

const SAMPLE_RATE = 16000;
// Fire an interim roughly every 500 ms of in-phrase audio.
const INTERIM_STRIDE_SAMPLES = Math.floor(SAMPLE_RATE * 0.5);

// Library fetches silero_vad.onnx + worklet + onnxruntime-web wasm from
// these base paths. We bundle them under frontend/public/vad/ so Electron
// (which serves from a local dev server or file://) works the same as web.
const ASSET_BASE = "/vad/";

// Module-level registry of every MediaStream this module has acquired.
// Belt-and-suspenders: even if a `MicVadHandle.destroy()` was missed
// somehow (e.g. a previous block instance whose React cleanup didn't
// fire after an HMR swap), `stopAllMicStreams()` enumerates and stops
// every track this module ever opened. ambient_mic.js calls it on
// mute/cleanup.
const _liveStreams: Set<MediaStream> = new Set();

export function stopAllMicStreams(): void {
  if (_liveStreams.size === 0) return;
  let total = 0;
  for (const s of Array.from(_liveStreams)) {
    for (const t of s.getTracks()) {
      try { t.stop(); total++; } catch { /* noop */ }
    }
    _liveStreams.delete(s);
  }
  console.log(`[vad.stopAll] stopped ${total} mic track(s)`);
}

export async function createMicVad(opts: MicVadOptions): Promise<MicVadHandle> {
  // Phase-level timing so we can see where the cold start goes.
  // The dominant costs are (1) the dynamic import + wasm/onnx fetches the
  // library does internally (VadPrewarm in the root layout should warm
  // these into the HTTP cache before we get here), and (2) the OS-level
  // getUserMedia permission negotiation on first mic access.
  const tInit = performance.now();
  const mod = await import("@ricky0123/vad-web");
  const tAfterImport = performance.now();

  let phraseId = 0;
  let inSpeech = false;
  let phraseBuffer: Float32Array[] = [];
  let phraseBufferLen = 0;
  let lastInterimAt = 0;

  const resetPhrase = () => {
    phraseBuffer = [];
    phraseBufferLen = 0;
    lastInterimAt = 0;
  };

  // Hold our own reference to the MediaStream so we can force-stop tracks
  // regardless of MicVAD's internal state machine. The library's
  // `destroy()` only calls `pauseStream(stream)` when `listening === true`
  // (real-time-vad.js:301-315). If the caller destroys mid-init or before
  // any speech, the library skips track.stop() and the OS-level mic
  // indicator (orange dot on macOS) stays on. We override `getStream` /
  // `pauseStream` to capture + stop the stream ourselves, then on
  // destroy we re-stop unconditionally (track.stop is idempotent).
  let ownStream: MediaStream | null = null;

  const stopOwnStream = () => {
    if (!ownStream) {
      console.log("[vad.destroy] no ownStream to stop");
      return;
    }
    const n = ownStream.getTracks().length;
    for (const track of ownStream.getTracks()) {
      try { track.stop(); } catch { /* noop */ }
    }
    _liveStreams.delete(ownStream);
    ownStream = null;
    console.log(`[vad.destroy] stopped ${n} mic track(s)`);
  };

  const vad: MicVAD = await mod.MicVAD.new({
    ...VAD_TUNING,
    baseAssetPath: ASSET_BASE,
    onnxWASMBasePath: ASSET_BASE,
    getStream: async () => {
      const tGum = performance.now();
      ownStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Acoustic echo cancellation — the browser/OS subtracts the
          // device's own speaker output from the mic capture. Without
          // this, the teacher's TTS plays through the speakers, gets
          // re-captured by the mic, and the transcript comes back as
          // if the user said it. Belt-and-suspenders with the
          // server-side echo dedup in infra/perception/cache.py.
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
          // Chromium-only hint: prefer the OS's communications-mode
          // AEC (Apple Audio Toolbox / Windows WASAPI) over the
          // browser's software AEC. Markedly better on built-in
          // laptop speakers. `ideal` (not `exact`) so non-Chromium
          // browsers silently ignore the unknown constraint instead
          // of failing the whole getUserMedia call.
          echoCancellationType: { ideal: "system" },
        } as MediaTrackConstraints,
      });
      console.log(
        `[vad.init] getUserMedia: ${Math.round(performance.now() - tGum)}ms`
      );
      _liveStreams.add(ownStream);
      return ownStream;
    },
    pauseStream: async (s: MediaStream) => {
      for (const track of s.getTracks()) {
        try { track.stop(); } catch { /* noop */ }
      }
      _liveStreams.delete(s);
      if (ownStream === s) ownStream = null;
    },
    resumeStream: async () => {
      ownStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Acoustic echo cancellation — the browser/OS subtracts the
          // device's own speaker output from the mic capture. Without
          // this, the teacher's TTS plays through the speakers, gets
          // re-captured by the mic, and the transcript comes back as
          // if the user said it. Belt-and-suspenders with the
          // server-side echo dedup in infra/perception/cache.py.
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
          // Chromium-only hint: prefer the OS's communications-mode
          // AEC (Apple Audio Toolbox / Windows WASAPI) over the
          // browser's software AEC. Markedly better on built-in
          // laptop speakers. `ideal` (not `exact`) so non-Chromium
          // browsers silently ignore the unknown constraint instead
          // of failing the whole getUserMedia call.
          echoCancellationType: { ideal: "system" },
        } as MediaTrackConstraints,
      });
      _liveStreams.add(ownStream);
      return ownStream;
    },

    onSpeechStart: () => {
      phraseId += 1;
      inSpeech = true;
      resetPhrase();
      opts.onSpeechStart?.(phraseId);
    },

    onFrameProcessed: (_probs, frame) => {
      if (!inSpeech) return;
      // Always buffer so flush() can finalize PTT releases mid-sentence.
      // Interim emission stays gated on whether the caller subscribed.
      // Copy the frame — the library reuses the buffer between callbacks.
      phraseBuffer.push(new Float32Array(frame));
      phraseBufferLen += frame.length;

      if (opts.onInterim && phraseBufferLen - lastInterimAt >= INTERIM_STRIDE_SAMPLES) {
        lastInterimAt = phraseBufferLen;
        const merged = concat(phraseBuffer, phraseBufferLen);
        try {
          opts.onInterim(encodeWavPcm16(merged, SAMPLE_RATE), phraseId);
        } catch (err) {
          opts.onError?.(err);
        }
      }
    },

    onSpeechEnd: (audio: Float32Array) => {
      inSpeech = false;
      resetPhrase();
      try {
        opts.onPhrase(encodeWavPcm16(audio, SAMPLE_RATE), phraseId);
      } catch (err) {
        opts.onError?.(err);
      }
    },

    onVADMisfire: () => {
      inSpeech = false;
      resetPhrase();
    },
  });
  const tDone = performance.now();
  console.log(
    `[vad.init] import: ${Math.round(tAfterImport - tInit)}ms, ` +
      `MicVAD.new (incl. getUserMedia + assets): ${Math.round(tDone - tAfterImport)}ms, ` +
      `total: ${Math.round(tDone - tInit)}ms`
  );

  return {
    start: () => vad.start(),
    pause: () => vad.pause(),
    flush: () => {
      // Synthesize an onSpeechEnd from whatever frames are buffered. The
      // underlying silero-vad only fires onSpeechEnd after redemptionFrames
      // (640ms) of silence, which never happens when the user releases
      // PTT mid-sentence. Reset state *before* invoking onPhrase so the
      // callback can synchronously trigger another mode transition.
      if (!inSpeech) return;
      const id = phraseId;
      const len = phraseBufferLen;
      const chunks = phraseBuffer;
      inSpeech = false;
      resetPhrase();
      if (len === 0) return;
      const merged = concat(chunks, len);
      try {
        opts.onPhrase(encodeWavPcm16(merged, SAMPLE_RATE), id);
      } catch (err) {
        opts.onError?.(err);
      }
    },
    destroy: () => {
      // Best-effort library teardown first so it can release ONNX/audio
      // resources cleanly. We don't await — `vad.destroy()` returns a
      // Promise that may resolve later, but we want the mic indicator
      // off NOW. Then unconditionally stop our captured tracks. Both
      // calls are idempotent.
      try { void vad.destroy(); } catch { /* noop */ }
      stopOwnStream();
    },
  };
}

function concat(chunks: Float32Array[], totalLen: number): Float32Array {
  const out = new Float32Array(totalLen);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

// Encode float32 PCM [-1, 1] → mono 16-bit PCM WAV Blob.
export function encodeWavPcm16(samples: Float32Array, sampleRate: number): Blob {
  const byteLength = 44 + samples.length * 2;
  const buf = new ArrayBuffer(byteLength);
  const view = new DataView(buf);

  writeString(view, 0, "RIFF");
  view.setUint32(4, byteLength - 8, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);

  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }
  return new Blob([buf], { type: "audio/wav" });
}

function writeString(view: DataView, offset: number, s: string) {
  for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
}
