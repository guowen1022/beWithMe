// JS interface for the native AudioTrackPlayerModule. Streaming write of raw
// PCM16LE chunks at a given sample rate. The Kotlin module ships in
// android/app/src/main/java/com/bewithme/AudioTrackPlayerModule.kt
// (Phase 1 step 5).

import { NativeModules, Platform } from "react-native";

interface NativePlayer {
  ensureStream: (sampleRate: number) => Promise<void>;
  writePcm16: (base64: string) => Promise<void>;
  flush: () => Promise<void>;
  stop: () => Promise<void>;
}

const NATIVE = (NativeModules as Record<string, unknown>).AudioTrackPlayerModule as NativePlayer | undefined;

function uint8ToBase64(bytes: Uint8Array): string {
  // RN ships btoa but it expects a binary string. Build it without spreading
  // large arrays onto the call stack.
  let s = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)));
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (globalThis as any).btoa(s);
}

export async function ensurePlayer(sampleRate: number): Promise<void> {
  if (!NATIVE) {
    throw new Error(
      "AudioTrackPlayerModule not linked — Phase 1 step 5 hasn't landed yet. " +
      "Native module path: android/app/src/main/java/com/bewithme/AudioTrackPlayerModule.kt",
    );
  }
  await NATIVE.ensureStream(sampleRate);
}

export async function writePcm(bytes: Uint8Array): Promise<void> {
  if (!NATIVE) return;
  await NATIVE.writePcm16(uint8ToBase64(bytes));
}

export async function flushPlayer(): Promise<void> {
  if (!NATIVE) return;
  await NATIVE.flush();
}

export async function stopPlayer(): Promise<void> {
  if (!NATIVE) return;
  await NATIVE.stop();
}

export function isPlayerAvailable(): boolean {
  return !!NATIVE && Platform.OS === "android";
}
