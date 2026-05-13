// Kokoro TTS response. Backend streams raw PCM16LE chunks with X-Sample-Rate
// header. RN's fetch doesn't expose response.body as a ReadableStream reliably
// (even with reactNative.textStreaming), so we buffer the full response and
// hand the caller a single Uint8Array + the sample rate. Higher TTFA than
// streaming, but reliable.

import { api } from "./client";

export interface SpeakOptions {
  voice?: string;
  speed?: number;
  lang?: string;
  signal?: AbortSignal;
}

export interface SpeakBufferHandle {
  sampleRate: number;
  pcm: Uint8Array;
}

export async function speakTextStream(text: string, opts: SpeakOptions = {}): Promise<SpeakBufferHandle> {
  const { signal, ...body } = opts;
  const res = await api("/api/speak/stream", {
    method: "POST",
    body: JSON.stringify({ text, ...body }),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`speakTextStream failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  const sampleRate = Number(res.headers.get("X-Sample-Rate")) || 24000;
  const buf = await res.arrayBuffer();
  return { sampleRate, pcm: new Uint8Array(buf) };
}
