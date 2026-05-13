// JS interface for the native AudioRecorderModule. The Kotlin module accumulates
// PCM16LE samples to a WAV file in the app cache while frames are being emitted;
// on stop() it returns the file:// URI for the captured turn.

import { NativeEventEmitter, NativeModules, Platform } from "react-native";

export type Int16Frame = Int16Array;

export interface RecorderOptions {
  sampleRate?: number;      // default 16000
  frameSamples?: number;    // default 512
  /** If false, skip Kotlin's per-session WAV accumulator. Ambient mode does
   *  per-phrase WAV writes from JS after VAD detects boundaries, so it sets
   *  this to false to avoid an unbounded WAV file across the whole session. */
  accumulateWav?: boolean;
}

export interface RecorderHandle {
  /** Stops recording. Returns a file:// URI for the captured WAV, or null if
   *  the recorder produced no samples. */
  stop(): Promise<string | null>;
  onFrame(handler: (frame: Int16Frame) => void): () => void;
}

// Backwards-compat alias.
export type RecorderHandleV2 = RecorderHandle;

const NATIVE = (NativeModules as Record<string, unknown>).AudioRecorderModule as
  | {
      start: (opts: { sampleRate: number; frameSamples: number; accumulateWav: boolean }) => Promise<void>;
      stop: () => Promise<string | null>;
    }
  | undefined;

export async function startRecording(opts: RecorderOptions = {}): Promise<RecorderHandle> {
  if (!NATIVE) {
    throw new Error(
      "AudioRecorderModule not linked — rebuild with `npx expo run:android`. " +
      "Native module: android/app/src/main/java/com/bewithme/mobile/AudioRecorderModule.kt",
    );
  }
  await NATIVE.start({
    sampleRate: opts.sampleRate ?? 16000,
    frameSamples: opts.frameSamples ?? 512,
    accumulateWav: opts.accumulateWav ?? true,
  });

  const emitter = new NativeEventEmitter(NATIVE as unknown as ConstructorParameters<typeof NativeEventEmitter>[0]);
  const handlers = new Set<(frame: Int16Frame) => void>();
  const sub = emitter.addListener("AudioRecorderFrame", (event: { data: number[] }) => {
    const frame = Int16Array.from(event.data);
    for (const h of handlers) h(frame);
  });

  return {
    async stop() {
      sub.remove();
      handlers.clear();
      return await NATIVE!.stop();
    },
    onFrame(handler) {
      handlers.add(handler);
      return () => handlers.delete(handler);
    },
  };
}

export function isRecorderAvailable(): boolean {
  return !!NATIVE && Platform.OS === "android";
}
