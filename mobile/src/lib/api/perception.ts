// Maps to services/persona/routers/perception_utterance.py. Fires UserSpeechEvent
// on the backend, which wakes the teacher's trigger orchestrator (Lane A).

import { api } from "./client";

export interface UtteranceRequest {
  text: string;
  language?: string;
  audio_duration_s?: number;
  target_persona?: "teacher" | string;
}

export interface UtteranceResponse {
  accepted: boolean;
  recorded?: boolean;
  reason?: string;
}

export async function postUtterance(req: UtteranceRequest): Promise<UtteranceResponse> {
  const res = await api("/api/perception/utterance", {
    method: "POST",
    body: JSON.stringify({
      text: req.text,
      language: req.language ?? "en",
      audio_duration_s: req.audio_duration_s ?? 0,
      target_persona: req.target_persona ?? "teacher",
    }),
  });
  if (!res.ok) throw new Error(`postUtterance failed (${res.status})`);
  return res.json();
}
