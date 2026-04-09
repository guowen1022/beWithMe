const API_BASE = "/api";
const API_STREAM = "/api"; // use Next.js route handler for SSE

export interface Profile {
  self_description: string;
  created_at: string;
}

export interface Interaction {
  id: string;
  session_id: string;
  passage_text: string | null;
  question: string;
  answer: string;
  source_document: string | null;
  created_at: string;
}

export interface AskRequest {
  passage_text?: string;
  selected_text?: string;
  question: string;
  document_id?: string;
  session_id?: string;
}

export interface AskResponse {
  interaction_id: string;
  answer: string;
  session_id: string;
  related_interaction_ids: string[];
}

export async function getProfile(): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profile`);
  if (!res.ok) throw new Error("Failed to fetch profile");
  return res.json();
}

export async function updateProfile(
  self_description: string
): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ self_description }),
  });
  if (!res.ok) throw new Error("Failed to update profile");
  return res.json();
}

export type StatusEvent = {
  type: "status";
  status: "thinking" | "searching" | "done";
  detail: string | null;
};

export type AnswerEvent = {
  type: "answer";
  answer: string;
  related_interaction_ids: string[];
};

export type StreamEvent = StatusEvent | AnswerEvent;

export async function askStream(
  req: AskRequest,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const res = await fetch(`${API_STREAM}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error("Failed to get answer");

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event = JSON.parse(line.slice(6));
          onEvent(event);
        } catch {
          // skip malformed
        }
      }
    }
  }
}

export async function ask(req: AskRequest): Promise<AskResponse> {
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error("Failed to get answer");
  return res.json();
}

export async function getInteractions(
  limit = 20,
  offset = 0
): Promise<Interaction[]> {
  const res = await fetch(
    `${API_BASE}/interactions?limit=${limit}&offset=${offset}`
  );
  if (!res.ok) throw new Error("Failed to fetch interactions");
  return res.json();
}
