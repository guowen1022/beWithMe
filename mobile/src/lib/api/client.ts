// Thin fetch wrapper. Adds base URL, auth headers, device headers, and 401
// handling. Matches frontend/lib/api.ts:authHeaders + deviceHeaders shape.

import {
  getBaseUrl,
  getUserId,
  getDeviceId,
  clearUserId,
  getOutputDeviceId,
  getSessionToken,
  setSessionToken,
} from "../../config";
import { getDeviceClass } from "../device/deviceClass";

export class UnknownUserError extends Error {
  constructor() { super("unknown_user"); this.name = "UnknownUserError"; }
}

/**
 * Attach identity to a header bag.
 *
 * Both headers are sent so one build works against either backend auth mode:
 * strict ignores X-User-Id and trusts only the bearer token; legacy has no
 * token and uses the header. See docs/SECURITY.md.
 */
export function applyIdentity(headers: Record<string, string>): void {
  const userId = getUserId();
  if (userId) headers["X-User-Id"] = userId;
  const token = getSessionToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  applyIdentity(headers);
  return headers;
}

/**
 * Exchange a user id (plus the deployment's access key in strict mode) for a
 * signed session token. Best-effort: a legacy deployment has nothing to issue,
 * and the X-User-Id path still works there.
 */
export async function startSession(userId: string, accessKey?: string): Promise<boolean> {
  try {
    const res = await fetch(url("/api/auth/session"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, access_key: accessKey ?? null }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (typeof data?.token === "string") {
      await setSessionToken(data.token);
      return true;
    }
  } catch {
    // Offline or legacy mode — fall through to the header path.
  }
  return false;
}

export function deviceHeaders(): Record<string, string> {
  const id = getDeviceId();
  if (!id) return {};
  const headers: Record<string, string> = {
    "X-Device-Id": id,
    "X-Device-Class": getDeviceClass(),
    "X-Device-Capabilities": JSON.stringify({ display: true, speaker: true, mic: true }),
  };
  // Cross-device output routing: phone-input answers delivered to another
  // device (typically a desktop running the web frontend).
  const out = getOutputDeviceId();
  if (out) headers["X-Output-Device-Id"] = out;
  return headers;
}

export function url(path: string): string {
  const base = getBaseUrl();
  if (!base) throw new Error("baseUrl not configured");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function throwIfUnknownUser(res: Response): Promise<void> {
  if (res.status === 401) {
    await clearUserId();
    throw new UnknownUserError();
  }
}

export interface ApiInit {
  method?: string;
  body?: string | FormData;
  signal?: AbortSignal;
  multipart?: boolean;
  stream?: boolean;
}

export async function api(path: string, init: ApiInit = {}): Promise<Response> {
  const headers: Record<string, string> = init.multipart
    ? {} // FormData sets its own Content-Type with boundary
    : { ...authHeaders() };
  if (init.multipart) {
    // FormData sets its own Content-Type, so identity is attached separately.
    applyIdentity(headers);
  }
  Object.assign(headers, deviceHeaders());

  // RN streaming fetch: requires reactNative.textStreaming on Hermes/new arch.
  const fetchInit: RequestInit & { reactNative?: { textStreaming?: boolean } } = {
    method: init.method ?? "GET",
    headers,
    body: init.body,
    signal: init.signal,
  };
  if (init.stream) fetchInit.reactNative = { textStreaming: true };

  const res = await fetch(url(path), fetchInit);
  await throwIfUnknownUser(res);
  return res;
}
