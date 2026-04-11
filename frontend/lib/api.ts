const API_BASE = "/api";
const API_STREAM = "/api"; // use Next.js route handler for SSE

// --- User management ---

let currentUserId: string | null = null;

export function setCurrentUserId(id: string) {
  currentUserId = id;
  if (typeof window !== "undefined") {
    localStorage.setItem("bewithme_user_id", id);
  }
}

export function getCurrentUserId(): string | null {
  if (!currentUserId && typeof window !== "undefined") {
    currentUserId = localStorage.getItem("bewithme_user_id");
  }
  return currentUserId;
}

export function clearCurrentUserId() {
  currentUserId = null;
  if (typeof window !== "undefined") {
    localStorage.removeItem("bewithme_user_id");
  }
}

export class UnknownUserError extends Error {
  constructor() {
    super("unknown_user");
    this.name = "UnknownUserError";
  }
}

async function throwIfUnknownUser(res: Response) {
  if (res.status === 401) {
    clearCurrentUserId();
    throw new UnknownUserError();
  }
}

function authHeaders(): Record<string, string> {
  const userId = getCurrentUserId();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (userId) {
    headers["X-User-Id"] = userId;
  }
  return headers;
}

export interface User {
  id: string;
  username: string;
  created_at: string;
}

export async function createUser(username: string): Promise<User> {
  const res = await fetch(`${API_BASE}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to create user");
  }
  return res.json();
}

export async function listUsers(): Promise<User[]> {
  const res = await fetch(`${API_BASE}/users`);
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

// --- Profile ---

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
  const res = await fetch(`${API_BASE}/profile`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch profile");
  return res.json();
}

export async function updateProfile(
  self_description: string
): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profile`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify({ self_description }),
  });
  await throwIfUnknownUser(res);
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
    headers: authHeaders(),
    body: JSON.stringify(req),
  });
  await throwIfUnknownUser(res);
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
    headers: authHeaders(),
    body: JSON.stringify(req),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to get answer");
  return res.json();
}

export async function getInteractions(
  limit = 20,
  offset = 0
): Promise<Interaction[]> {
  const res = await fetch(
    `${API_BASE}/interactions?limit=${limit}&offset=${offset}`,
    { headers: authHeaders() }
  );
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch interactions");
  return res.json();
}

export interface Preferences {
  explanation_style: string;
  depth_preference: string;
  analogy_affinity: string;
  math_comfort: string;
  pacing: string;
  meta_notes: string;
  interaction_count: number;
  last_distilled_at: string | null;
}

export async function getPreferences(): Promise<Preferences> {
  const res = await fetch(`${API_BASE}/preferences`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch preferences");
  return res.json();
}

export async function distillPreferences(): Promise<Preferences> {
  const res = await fetch(`${API_BASE}/preferences/distill`, {
    method: "POST",
    headers: authHeaders(),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to distill preferences");
  return res.json();
}

export interface Concept {
  id: string;
  name: string;
  state: string;
  encounter_count: number;
  first_seen: string;
  last_seen: string;
}

export async function getConcepts(): Promise<Concept[]> {
  const res = await fetch(`${API_BASE}/concepts`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch concepts");
  return res.json();
}

export interface GraphNode {
  id: string;
  state: string;
  mastery: number;
  encounters: number;
  halfLife: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  type: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export async function getGraphData(): Promise<GraphData> {
  const res = await fetch(`${API_BASE}/graph`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch graph");
  return res.json();
}
