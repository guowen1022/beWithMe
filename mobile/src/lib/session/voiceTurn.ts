// One voice turn: phrase audio → transcribe → utterance → askStream →
// sentence-buffered speak. The function is mode-agnostic; PTT and ambient
// both call it with the same payload after their respective phrase ends.
//
// Returns when the speak stream for the final sentence has been queued. Use
// the returned AbortController to cancel mid-turn (e.g. barge-in).

import { transcribeAudio } from "../api/transcribe";
import { postUtterance } from "../api/perception";
import { askStream, type StreamEvent } from "../api/ask";
import { speakTextStream } from "../api/speak";
import { SentenceBuffer } from "../audio/sentenceBuffer";
import { ensurePlayer, writePcm } from "../audio/player";
import { getOutputDeviceId } from "../../config";
import type { VoiceMode } from "../../state/mode";

export interface VoiceTurnCallbacks {
  setMode: (m: VoiceMode) => void;
  onTranscript?: (text: string) => void;
  onAnswer?: (text: string) => void;
  onError?: (err: unknown) => void;
}

export async function runVoiceTurn(
  wavUri: string,
  cb: VoiceTurnCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  try {
    cb.setMode("transcribing");
    const trans = await transcribeAudio({ uri: wavUri, filename: "audio.wav" });
    if (signal?.aborted) return;
    const text = trans.text.trim();
    if (!text || isNoiseTranscript(text)) {
      // Whisper hallucinates on silence/clicks — common outputs are tiny
      // filler tokens. Drop these so they don't reach the persona and
      // trigger spurious "hi, I can help with..." intro turns.
      cb.setMode("idle");
      return;
    }
    cb.onTranscript?.(text);

    // Fire perception/utterance for echo dedup + perception cache. Don't
    // block the ask call on it — they're parallel paths on the backend too.
    postUtterance({
      text,
      language: "en",
      audio_duration_s: trans.duration_seconds,
      target_persona: "teacher",
    }).catch((e) => console.warn("[voiceTurn] postUtterance failed:", e));

    cb.setMode("thinking");
    let answerStarted = false;
    // When the user has routed output to a peer device, the backend's
    // auto-speak path already delivers TTS to that device via voice-play
    // SSE events. The phone must NOT also play locally — it just waits
    // for the askStream to complete, then drops back to idle/ambient.
    const remoteOutput = getOutputDeviceId() !== null;
    const speakChain = remoteOutput ? null : createSpeakChain(cb, signal);
    const sentenceBuf = new SentenceBuffer((sentence) => {
      speakChain?.enqueue(sentence);
    });

    await askStream(
      { question: text },
      (event: StreamEvent) => {
        if (signal?.aborted) return;
        if (event.type === "token") {
          if (!answerStarted) { answerStarted = true; }
          const text = (event as { text?: unknown }).text;
          if (typeof text === "string") sentenceBuf.append(text);
        } else if (event.type === "answer") {
          const answer = (event as { answer?: unknown }).answer;
          if (typeof answer === "string") cb.onAnswer?.(answer);
        }
      },
      signal,
    );
    sentenceBuf.flush();
    if (speakChain) await speakChain.drain();
    cb.setMode("idle");
  } catch (err) {
    if (signal?.aborted) return;
    cb.onError?.(err);
    cb.setMode("idle");
  }
}

// Common Whisper-on-silence hallucinations. If the transcript is just one
// of these (modulo punctuation/case), the user wasn't really speaking and
// we shouldn't bother the persona with it.
const _NOISE_SET = new Set([
  "", ".", "..", "...", "um", "uh", "mhm", "mm", "hmm", "hm", "yeah",
  "ok", "okay", "bye", "thank you", "thanks", "you", "hi", "hello",
  "[blank_audio]", "(blank_audio)", "(silence)", "[silence]",
]);

function isNoiseTranscript(text: string): boolean {
  const norm = text.toLowerCase().replace(/[.,!?¿¡。、,]+/g, "").trim();
  if (!norm) return true;
  if (norm.length < 3) return true;
  return _NOISE_SET.has(norm);
}

// Speak chain: serialize per-sentence /api/speak/stream calls so playback
// stays in answer order. We buffer each sentence's PCM and write it to the
// AudioTrack in arrival order; chain drains when all sentences finish.
function createSpeakChain(cb: VoiceTurnCallbacks, signal?: AbortSignal) {
  let chain: Promise<void> = Promise.resolve();
  return {
    enqueue(sentence: string): void {
      chain = chain.then(async () => {
        if (signal?.aborted) return;
        try {
          const { sampleRate, pcm } = await speakTextStream(sentence, { signal });
          if (signal?.aborted) return;
          await ensurePlayer(sampleRate);
          cb.setMode("speaking");
          if (pcm.byteLength > 0) await writePcm(pcm);
        } catch (e) {
          if (signal?.aborted) return;
          console.warn("[voiceTurn] speak chain error:", e);
        }
      });
    },
    drain(): Promise<void> { return chain; },
  };
}
