// EOU (end-of-utterance) client — text-only turn detector hosted by the
// transcribe sidecar. See infra/contracts/transcribe.py for the wire format.
//
// Fail-open: any network/HTTP error resolves to {endOfTurn: true, endProb: 1}.
// The downstream gate would otherwise hold the user's turn forever on a
// transient failure — far worse UX than the disfluency churn we're fixing.

export interface EouTurn {
  role: "user" | "assistant";
  text: string;
}

export interface EouCheckArgs {
  transcripts: string[];
  priorTurns?: EouTurn[];
  threshold?: number;
  signal?: AbortSignal;
}

export interface EouCheckResult {
  endProb: number;
  endOfTurn: boolean;
  threshold: number;
  inferMs: number;
}

const FAIL_OPEN: EouCheckResult = {
  endProb: 1,
  endOfTurn: true,
  threshold: 0,
  inferMs: 0,
};

export async function checkEou(args: EouCheckArgs): Promise<EouCheckResult> {
  try {
    const res = await fetch("/api/eou", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transcripts: args.transcripts,
        prior_turns: args.priorTurns ?? [],
        threshold: args.threshold,
      }),
      signal: args.signal,
    });
    if (!res.ok) {
      // 503 means EOU isn't configured — that's a normal "feature off"
      // state, not an error worth surfacing to the user.
      if (res.status !== 503) {
        console.warn(`[eou] /api/eou returned ${res.status}`);
      }
      return FAIL_OPEN;
    }
    const data = await res.json();
    return {
      endProb: Number(data.end_prob ?? 1),
      endOfTurn: Boolean(data.end_of_turn ?? true),
      threshold: Number(data.threshold ?? 0),
      inferMs: Number(data.infer_ms ?? 0),
    };
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") {
      // Surface aborts so the gate can drop the stale check silently.
      throw err;
    }
    console.warn("[eou] check failed, failing open:", err);
    return FAIL_OPEN;
  }
}
