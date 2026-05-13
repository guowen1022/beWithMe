// Matches frontend/lib/api.ts:transcribeAudio. Multipart upload of a WAV blob;
// returns the Whisper transcript. Does NOT set Content-Type — FormData on RN
// auto-generates the multipart boundary.

import { api } from "./client";

export interface TranscribeResult {
  text: string;
  duration_seconds: number;
}

export interface WavUpload {
  uri?: string;       // file:// URI on Android, when saved to disk
  data?: ArrayBuffer; // raw bytes, when held in memory
  filename?: string;
}

export async function transcribeAudio(
  wav: WavUpload,
  language: string = "en",
  initialPrompt: string = "",
): Promise<TranscribeResult> {
  const fd = new FormData();
  const filename = wav.filename ?? "audio.wav";
  if (wav.uri) {
    // RN supports the {uri, name, type} shape on FormData
    fd.append("file", { uri: wav.uri, name: filename, type: "audio/wav" } as unknown as Blob);
  } else if (wav.data) {
    // Wrap ArrayBuffer in a Blob (RN >= 0.71 supports Blob from ArrayBuffer).
    fd.append("file", new Blob([wav.data], { type: "audio/wav" }), filename);
  } else {
    throw new Error("transcribeAudio: need wav.uri or wav.data");
  }
  fd.append("language", language);
  if (initialPrompt) fd.append("initial_prompt", initialPrompt);

  const res = await api("/api/transcribe", {
    method: "POST",
    body: fd,
    multipart: true,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Transcription failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  return res.json();
}
