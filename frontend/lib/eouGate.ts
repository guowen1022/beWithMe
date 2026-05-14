// Turn-merging gate sitting between silero-VAD phrase commits and the
// persona. Each transcribed phrase calls `ingest(text, phraseId, durationS)`;
// the gate scores the rolling transcript via /api/eou and only fires
// `onCommit(mergedText, lastPhraseId, totalDurationS)` once the user
// actually sounds done. Disfluent pauses ("ummm... ah...") that would
// otherwise commit a fragment now stay buffered until the next phrase
// (or the hard timeout) resolves them.
//
// Fail-open everywhere — if the EOU service is down or misconfigured,
// the gate commits each phrase immediately so we degrade to today's
// behavior, not to a stuck conversation.

import { checkEou, type EouTurn } from "@/lib/eou";

export interface EouGateOptions {
  /** Fires when the merged turn is judged complete. */
  onCommit(args: {
    text: string;
    phraseId: number;
    totalDurationS: number;
    phrases: string[];
  }): void | Promise<void>;
  /** Optional conversation history fed to the model alongside the rolling turn. */
  priorTurns?(): EouTurn[];
  /** Override the server-side default (0.55). */
  threshold?: number;
  /** Force-commit if no high-confidence EOU within this many ms of the
   *  last ingest. Defaults to 4500. */
  hardTimeoutMs?: number;
  /** Force-commit when the buffer reaches this many phrases. Defaults to 6. */
  maxPhrases?: number;
  /** Fires whenever the gate commits, used by callers that want to
   *  observe the decision without owning the commit. */
  onDecision?(info: {
    reason: "eou" | "timeout" | "max_phrases" | "flush";
    phrases: number;
    endProb?: number;
  }): void;
}

export interface EouGate {
  /** Push a freshly-transcribed phrase. Triggers an async EOU check. */
  ingest(text: string, phraseId: number, durationS?: number): void;
  /** Commit whatever is buffered right now (e.g. block unmount / mute). */
  flush(): void;
  /** Drop buffered state without committing (e.g. barge-in canceled). */
  reset(): void;
  /** Has the gate accumulated phrases pending an EOU decision? */
  pending(): boolean;
}

const DEFAULT_HARD_TIMEOUT_MS = 4500;
const DEFAULT_MAX_PHRASES = 6;

export function createEouGate(opts: EouGateOptions): EouGate {
  const hardTimeoutMs = opts.hardTimeoutMs ?? DEFAULT_HARD_TIMEOUT_MS;
  const maxPhrases = opts.maxPhrases ?? DEFAULT_MAX_PHRASES;

  let transcripts: string[] = [];
  let durations: number[] = [];
  let lastPhraseId = 0;
  let hardTimer: ReturnType<typeof setTimeout> | null = null;
  let inFlight: AbortController | null = null;
  let destroyed = false;

  const clearHardTimer = () => {
    if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
  };

  const snapshotAndClear = (): { text: string; phrases: string[]; totalDurationS: number; phraseId: number } => {
    const snap = {
      text: transcripts.join(" ").trim(),
      phrases: transcripts.slice(),
      totalDurationS: durations.reduce((a, b) => a + b, 0),
      phraseId: lastPhraseId,
    };
    transcripts = [];
    durations = [];
    clearHardTimer();
    return snap;
  };

  const commit = (reason: "eou" | "timeout" | "max_phrases" | "flush", endProb?: number) => {
    if (transcripts.length === 0) return;
    if (inFlight) { inFlight.abort(); inFlight = null; }
    const snap = snapshotAndClear();
    opts.onDecision?.({ reason, phrases: snap.phrases.length, endProb });
    try {
      void opts.onCommit({
        text: snap.text,
        phraseId: snap.phraseId,
        totalDurationS: snap.totalDurationS,
        phrases: snap.phrases,
      });
    } catch (err) {
      console.warn("[eouGate] onCommit threw:", err);
    }
  };

  const armTimer = () => {
    clearHardTimer();
    hardTimer = setTimeout(() => commit("timeout"), hardTimeoutMs);
  };

  return {
    ingest(text: string, phraseId: number, durationS = 0) {
      if (destroyed) return;
      const clean = text.trim();
      if (!clean) return;
      // Dedupe identical trailing text — whisper occasionally repeats.
      const last = transcripts[transcripts.length - 1];
      if (last && last === clean) return;

      transcripts.push(clean);
      durations.push(durationS || 0);
      lastPhraseId = phraseId;

      if (transcripts.length >= maxPhrases) {
        commit("max_phrases");
        return;
      }
      armTimer();

      // A new phrase invalidates any in-flight check — its answer would
      // be about an outdated transcript.
      if (inFlight) inFlight.abort();
      const ctl = new AbortController();
      inFlight = ctl;
      const snapshotTranscripts = transcripts.slice();

      void (async () => {
        try {
          const result = await checkEou({
            transcripts: snapshotTranscripts,
            priorTurns: opts.priorTurns?.(),
            threshold: opts.threshold,
            signal: ctl.signal,
          });
          if (ctl.signal.aborted || destroyed) return;
          if (inFlight === ctl) inFlight = null;
          if (result.endOfTurn) {
            commit("eou", result.endProb);
          }
        } catch (err) {
          if ((err as { name?: string })?.name === "AbortError") return;
          console.warn("[eouGate] check error:", err);
        }
      })();
    },
    flush() {
      if (destroyed) return;
      commit("flush");
    },
    reset() {
      if (destroyed) return;
      if (inFlight) { inFlight.abort(); inFlight = null; }
      transcripts = [];
      durations = [];
      clearHardTimer();
    },
    pending() {
      return transcripts.length > 0;
    },
  };
}
