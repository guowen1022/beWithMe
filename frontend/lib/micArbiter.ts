// Mic arbiter — exactly one consumer holds the mic at a time.
//
// Two consumers exist today:
//   - "block":       the ambient_mic block's always-on VAD
//   - "questionbar": push-to-talk in QuestionBar
//
// QuestionBar wins while recording (it represents an explicit user
// gesture). The block subscribes; when QuestionBar acquires, the block
// pauses its VAD, then resumes when QuestionBar releases. Two MicVADs
// over the same `getUserMedia` track produce flaky transcription;
// gating here keeps it clean.

export type MicHolder = "block" | "questionbar";

let current: MicHolder | null = null;
const listeners = new Set<(holder: MicHolder | null) => void>();

function notify(): void {
  for (const fn of listeners) {
    try { fn(current); } catch (err) { console.error("[micArbiter] listener", err); }
  }
}

/** Try to take the mic. Returns true if granted. QuestionBar always wins. */
export function acquire(holder: MicHolder): boolean {
  if (current === holder) return true;
  if (current === null) {
    current = holder;
    notify();
    return true;
  }
  // QuestionBar preempts a block holder.
  if (holder === "questionbar" && current === "block") {
    current = "questionbar";
    notify();
    return true;
  }
  // Block does not preempt questionbar.
  return false;
}

/** Release the mic. No-op if held by someone else. */
export function release(holder: MicHolder): void {
  if (current !== holder) return;
  current = null;
  notify();
}

/** Read the current holder (no subscription). */
export function getHolder(): MicHolder | null {
  return current;
}

/** Subscribe to holder changes. Returns an unsubscribe fn. */
export function subscribe(fn: (holder: MicHolder | null) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
