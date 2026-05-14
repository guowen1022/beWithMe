// Mirrors frontend/lib/eouGate.ts. Keeping the file separate (rather than
// hoisting to a shared package) matches the existing mobile/frontend split
// for transcribe/perception/speak/ask clients — same trade-off applies.
//
// If you change one, change both. The exported API surface MUST stay
// identical so a future shared package refactor is mechanical.

import { checkEou, type EouTurn } from "./api/eou";

export interface EouGateOptions {
  onCommit(args: {
    text: string;
    phraseId: number;
    totalDurationS: number;
    phrases: string[];
  }): void | Promise<void>;
  priorTurns?(): EouTurn[];
  threshold?: number;
  hardTimeoutMs?: number;
  maxPhrases?: number;
  onDecision?(info: {
    reason: "eou" | "timeout" | "max_phrases" | "flush";
    phrases: number;
    endProb?: number;
  }): void;
}

export interface EouGate {
  ingest(text: string, phraseId: number, durationS?: number): void;
  flush(): void;
  reset(): void;
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

  const snapshotAndClear = () => {
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
          if (result.endOfTurn) commit("eou", result.endProb);
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
