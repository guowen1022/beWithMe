// Tracks whether the SpeakerSink is actively playing TTS. The
// ambient_mic block uses this to drop VAD phrases captured while the
// teacher is speaking — otherwise the teacher's own voice plays
// through the speakers, gets picked up by the mic, and comes back as
// a transcript ("user said: 'I don't see anything called Captain on
// your canvas right now…'", which is what the teacher actually said).
//
// Browser-level echo cancellation in getUserMedia handles a lot of
// this, but it's never perfect — quiet rooms with unmuted speakers
// still leak the teacher's own words back. Gating on playback state
// is the cheap, decisive fix.
//
// Settle period: we keep `isPlaying()` true for a short window after
// the last chunk ends so the audio that was already in flight when
// playback "ended" doesn't slip through into a phrase right after.

const SETTLE_MS = 400;

let _activeSources = 0;
let _settleTimer: ReturnType<typeof setTimeout> | null = null;
let _settling = false;
const _listeners = new Set<(playing: boolean) => void>();

function notify(): void {
  const v = isPlaying();
  for (const fn of _listeners) {
    try { fn(v); } catch (err) { console.error("[speakerState] listener", err); }
  }
}

/** Mark one TTS audio chunk as starting playback. Pair with `endChunk`. */
export function startChunk(): void {
  _activeSources += 1;
  if (_settleTimer) {
    clearTimeout(_settleTimer);
    _settleTimer = null;
  }
  _settling = false;
  notify();
}

/** Mark one TTS audio chunk as finished. When all active chunks have
 * finished, the settle timer arms `isPlaying() === false` after
 * SETTLE_MS so any tail-end audio doesn't leak through into a
 * fresh phrase. */
export function endChunk(): void {
  _activeSources = Math.max(0, _activeSources - 1);
  if (_activeSources > 0) return;
  _settling = true;
  if (_settleTimer) clearTimeout(_settleTimer);
  _settleTimer = setTimeout(() => {
    _settling = false;
    _settleTimer = null;
    notify();
  }, SETTLE_MS);
  // Don't notify yet — we're still "playing" until the settle elapses.
}

export function isPlaying(): boolean {
  return _activeSources > 0 || _settling;
}

export function subscribe(fn: (playing: boolean) => void): () => void {
  _listeners.add(fn);
  return () => { _listeners.delete(fn); };
}
