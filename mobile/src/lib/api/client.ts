// Thin fetch wrapper. Adds base URL, auth headers, device headers, and 401
// handling. Matches frontend/lib/api.ts:authHeaders + deviceHeaders shape.

import { getBaseUrl, getUserId, getDeviceId, clearUserId } from "../../config";
import { getDeviceClass } from "../device/deviceClass";

export class UnknownUserError extends Error {
  constructor() { super("unknown_user"); this.name = "UnknownUserError"; }
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const userId = getUserId();
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

export function deviceHeaders(): Record<string, string> {
  const id = getDeviceId();
  if (!id) return {};
  return {
    "X-Device-Id": id,
    "X-Device-Class": getDeviceClass(),
    "X-Device-Capabilities": JSON.stringify({ display: true, speaker: true, mic: true }),
  };
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
    const userId = getUserId();
    if (userId) headers["X-User-Id"] = userId;
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
