// EOU (end-of-utterance) client — mirrors frontend/lib/eou.ts. See
// infra/contracts/transcribe.py for the wire format.
//
// Fail-open: any network or HTTP error resolves to {endOfTurn: true, endProb: 1}.
// Holding the user's turn forever on a transient backend hiccup would be a
// worse bug than the disfluency churn we're fixing.

import { api } from "./client";

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
    const res = await api("/api/eou", {
      method: "POST",
      body: JSON.stringify({
        transcripts: args.transcripts,
        prior_turns: args.priorTurns ?? [],
        threshold: args.threshold,
      }),
      signal: args.signal,
    });
    if (!res.ok) {
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
    if ((err as { name?: string })?.name === "AbortError") throw err;
    console.warn("[eou] check failed, failing open:", err);
    return FAIL_OPEN;
  }
}
