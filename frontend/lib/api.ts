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
  parent_interaction_id?: string;
  prompt_version?: "v1" | "v2";
  /** Routing override. Default "teacher" runs the LLM intent router.
   *  "frontend_engineer" bypasses the router and goes straight to the
   *  engineer agent — used by the canvas test-mode toggle. */
  addressee?: "teacher" | "frontend_engineer";
}

export interface AskResponse {
  interaction_id: string;
  answer: string;
  session_id: string;
  title?: string | null;
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
  title: string | null;
  related_interaction_ids: string[];
};

export type TokenEvent = {
  type: "token";
  text: string;
};

export type TitleEvent = {
  type: "title";
  title: string;
};

export type InteractionEvent = {
  type: "interaction";
  interaction_id: string;
};

export type LlmUsage = {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
};

export type DebugEvent = {
  type: "debug";
  static_system: string;
  static_user_passage: string;
  dynamic_user: string;
  prior_message_count?: number;
  usage: LlmUsage;
};

export type StreamEvent =
  | StatusEvent
  | AnswerEvent
  | TokenEvent
  | TitleEvent
  | InteractionEvent
  | DebugEvent;

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

// --- Dynamic UI back-channel ---

export type DynamicEvent =
  | { type: "open" }
  | { type: "ui-update"; action: "mount" | "replace" | "unmount"; block: { id: string; source: string; design_doc?: string | null } }
  | { type: "block-data"; block_id: string; topic: string; value: unknown }
  | { type: "block-error"; block_id: string; error: string };

export async function subscribeToDynamicStream(
  onEvent: (event: DynamicEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_STREAM}/dynamic/stream`, {
    method: "GET",
    headers: authHeaders(),
    signal,
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error(`Failed to open dynamic stream (${res.status})`);

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
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as DynamicEvent);
      } catch {
        // skip malformed
      }
    }
  }
}

export async function fetchCanvas(): Promise<{ id: string; source: string; design_doc?: string | null }[]> {
  const res = await fetch(`${API_BASE}/dynamic/canvas`, {
    method: "GET",
    headers: authHeaders(),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error(`fetchCanvas failed (${res.status})`);
  return res.json();
}

export async function pushBlockData(
  blockId: string,
  topic: string,
  value: unknown,
): Promise<void> {
  const res = await fetch(`${API_BASE}/dynamic/push/${encodeURIComponent(blockId)}`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ topic, value }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error(`pushBlockData failed (${res.status})`);
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

// --- PDF / URL upload ---

export interface PdfUploadResult {
  id: string;
  title: string;
  filename: string | null;
  text: string;
  pages: number;
}

export async function uploadPdf(file: File): Promise<PdfUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  const userId = getCurrentUserId();
  const headers: Record<string, string> = {};
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  await throwIfUnknownUser(res);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to upload PDF");
  }
  return res.json();
}

export async function uploadUrl(url: string): Promise<PdfUploadResult> {
  const userId = getCurrentUserId();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${API_BASE}/documents/url`, {
    method: "POST",
    headers,
    body: JSON.stringify({ url }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to fetch URL");
  }
  return res.json();
}

// --- Browser handoff (captcha-solving) ---

export interface BrowserStatus {
  status: string;
  headed: boolean;
  pages: number;
  urls: string[];
}

export async function getBrowserStatus(): Promise<BrowserStatus> {
  const res = await fetch(`${API_BASE}/browser/status`);
  return res.json();
}

export async function getBrowserSelection(): Promise<{ selection: string; url: string }> {
  const res = await fetch(`${API_BASE}/browser/selection`);
  return res.json();
}

export async function browserHandoff(url: string): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/browser/handoff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to open browser");
  }
  return res.json();
}

export async function browserResume(): Promise<PdfUploadResult> {
  const userId = getCurrentUserId();
  const headers: Record<string, string> = {};
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${API_BASE}/browser/resume`, {
    method: "POST",
    headers,
  });
  await throwIfUnknownUser(res);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to extract content");
  }
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

// --- Sessions ---

export async function recordSignal(
  sessionId: string,
  parentInteractionId: string,
  blockText: string,
  signal: "got_it" | "review_later",
): Promise<void> {
  const res = await fetch(`${API_BASE}/interactions/signal`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      session_id: sessionId,
      parent_interaction_id: parentInteractionId,
      block_text: blockText,
      signal,
    }),
  });
  if (!res.ok) {
    console.error("[recordSignal] failed:", res.status);
  }
}

export async function endSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/end`, {
    method: "POST",
    headers: authHeaders(),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to end session");
}

// --- Session graph ---

export interface SessionNode {
  session_id: string;
  title: string;
  labels: string[];
  created_at: string;
  duration_min: number;
  summary: string;
}

export interface SessionGraphData {
  nodes: SessionNode[];
}

export async function getSessionGraph(label?: string): Promise<SessionGraphData> {
  const params = label ? `?label=${encodeURIComponent(label)}` : "";
  const res = await fetch(`${API_BASE}/sessions/summaries/graph${params}`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch session graph");
  return res.json();
}

export async function getGraphData(): Promise<GraphData> {
  const res = await fetch(`${API_BASE}/graph`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch graph");
  return res.json();
}

// --- Recommendations ---

export interface RecommendationItem {
  id: string;
  source: string;
  category: string;
  title: string;
  summary: string;
  reasoning: string;
  url: string | null;
  concept_names: string[];
  priority: number;
  status: string;
  created_at: string;
}

export async function getRecommendations(
  source?: string,
  category?: string
): Promise<RecommendationItem[]> {
  const params = new URLSearchParams();
  if (source) params.set("source", source);
  if (category) params.set("category", category);
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/recommendations${qs ? `?${qs}` : ""}`,
    { headers: authHeaders() }
  );
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch recommendations");
  return res.json();
}

export async function generateRecommendations(): Promise<RecommendationItem[]> {
  const res = await fetch(`${API_BASE}/recommendations/generate`, {
    method: "POST",
    headers: authHeaders(),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to generate recommendations");
  return res.json();
}

export async function updateRecommendation(
  id: string,
  status: "dismissed" | "accepted"
): Promise<RecommendationItem> {
  const res = await fetch(`${API_BASE}/recommendations/${id}`, {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify({ status }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to update recommendation");
  return res.json();
}

// --- Goal Planning ---

export interface DAGNode {
  id: string;
  label: string;
  type: "goal" | "prerequisite";
  status: "pending" | "known" | "unknown" | "expanded" | "atomic";
}

export interface DAGEdge {
  source: string;
  target: string;
}

export interface DAGData {
  nodes: DAGNode[];
  edges: DAGEdge[];
}

export interface GoalSummary {
  id: string;
  title: string;
  status: string;
  node_count: number;
  created_at: string;
}

export interface GoalFull {
  id: string;
  title: string;
  dag: DAGData;
  transcript: { role: string; text: string }[];
  status: string;
  created_at: string;
}

export interface GoalStepResult {
  id: string;
  dag: DAGData;
  transcript: { role: string; text: string }[];
  text: string;
}

export async function createGoal(title: string): Promise<GoalFull> {
  const res = await fetch(`${API_BASE}/goals`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ title }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to create goal");
  return res.json();
}

export async function expandNode(goalId: string, nodeId: string): Promise<GoalStepResult> {
  const res = await fetch(`${API_BASE}/goals/${goalId}/expand`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ node_id: nodeId }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to expand node");
  return res.json();
}

export async function feedbackNode(goalId: string, nodeId: string, action: "know" | "unknown"): Promise<GoalStepResult> {
  const res = await fetch(`${API_BASE}/goals/${goalId}/feedback`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ node_id: nodeId, action }),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to send feedback");
  return res.json();
}

export async function finalizeGoal(goalId: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API_BASE}/goals/${goalId}/finalize`, {
    method: "POST",
    headers: authHeaders(),
  });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to finalize goal");
  return res.json();
}

export async function listGoals(): Promise<GoalSummary[]> {
  const res = await fetch(`${API_BASE}/goals`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to list goals");
  return res.json();
}

export async function getGoal(goalId: string): Promise<GoalFull> {
  const res = await fetch(`${API_BASE}/goals/${goalId}`, { headers: authHeaders() });
  await throwIfUnknownUser(res);
  if (!res.ok) throw new Error("Failed to fetch goal");
  return res.json();
}

// --- Transcription (local Whisper) ---

export async function transcribeAudio(
  blob: Blob,
  language: string = "en",
  initialPrompt: string = "",
): Promise<{ text: string; duration_seconds: number }> {
  const fd = new FormData();
  const filename = blob.type.includes("wav") ? "audio.wav" : "audio.webm";
  fd.append("file", blob, filename);
  fd.append("language", language);
  if (initialPrompt) fd.append("initial_prompt", initialPrompt);
  // Don't use authHeaders() — that sets Content-Type: application/json, which
  // would override fetch's auto-generated multipart boundary on FormData.
  const userId = getCurrentUserId();
  const headers: Record<string, string> = {};
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${API_BASE}/transcribe`, {
    method: "POST",
    headers,
    body: fd,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Transcription failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  return res.json();
}

// --- TTS (local Kokoro) ---

export async function speakText(
  text: string,
  opts: { voice?: string; speed?: number; lang?: string } = {},
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/speak`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ text, ...opts }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`TTS failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  return res.blob();
}

export async function speakTextStream(
  text: string,
  opts: {
    voice?: string;
    speed?: number;
    lang?: string;
    signal?: AbortSignal;
  } = {},
): Promise<{ sampleRate: number; reader: ReadableStreamDefaultReader<Uint8Array> }> {
  const { signal, ...body } = opts;
  const res = await fetch(`${API_BASE}/speak/stream`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ text, ...body }),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`TTS stream failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  if (!res.body) {
    throw new Error("TTS stream failed: empty response body");
  }
  const sampleRate = Number(res.headers.get("X-Sample-Rate")) || 24000;
  return { sampleRate, reader: res.body.getReader() };
}
