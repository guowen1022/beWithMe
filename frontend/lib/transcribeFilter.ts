// Whisper-on-silence hallucination filter — mirror of the mobile
// implementation in mobile/src/lib/session/voiceTurn.ts (kept duplicated
// because RN and web can't share code directly; if you change one,
// change both).
//
// Whisper.cpp tends to emit a small set of filler tokens when fed
// silence, clicks, or background noise. Without this filter, every
// stray "uh" trips the persona's ambient response loop, which then
// dutifully verbalizes a refusal ("no action needed", "I'm here…").
//
// Keep this set in sync with mobile. If you find a new false-positive
// in production, add it here AND in voiceTurn.ts._NOISE_SET.

const NOISE_SET: ReadonlySet<string> = new Set([
  "", ".", "..", "...",
  "um", "uh", "mhm", "mm", "hmm", "hm",
  "yeah", "ok", "okay", "bye",
  "thank you", "thanks", "you",
  "hi", "hello",
  "[blank_audio]", "(blank_audio)", "(silence)", "[silence]",
]);

const PUNCT_RE = /[.,!?¿¡。、,]+/g;

export function isNoiseTranscript(text: string): boolean {
  const norm = (text || "").toLowerCase().replace(PUNCT_RE, "").trim();
  if (!norm) return true;
  if (norm.length < 3) return true;
  return NOISE_SET.has(norm);
}
