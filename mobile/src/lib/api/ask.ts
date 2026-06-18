// SSE stream of LLM tokens + final answer. Mirrors frontend/lib/api.ts:askStream.

import { streamSse } from "../sse/sseStream";

export interface AskRequest {
  question: string;
  passage_text?: string;
  selected_text?: string;
  document_id?: string;
  addressee?: string;
  session_id?: string;
}

export interface LlmUsage {
  prompt_tokens: number;
  completion_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

export type StreamEvent =
  | { type: "status"; status: string }
  | { type: "token"; text: string }
  | { type: "title"; title: string }
  | { type: "answer"; answer: string; title?: string }
  | { type: "interaction"; interaction_id: string }
  | { type: "debug"; static_system: string; static_user_passage: string; dynamic_user: string; usage: LlmUsage }
  | { type: string; [k: string]: unknown };

export async function askStream(
  req: AskRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamSse<StreamEvent>(
    {
      path: "/api/ask/stream",
      method: "POST",
      body: JSON.stringify(req),
      signal,
    },
    onEvent,
  );
}
