/**
 * Client-side event logger.
 *
 * Fires `{kind, fields}` at /api/events. The Next.js route handler forwards
 * to the knowledge sidecar, which appends a JSON line to logs/events.jsonl
 * alongside every other sidecar event. Use this for UI-side actions —
 * button clicks, ask submits (with input modality), recording start/stop,
 * page navigations — anything you'd want to see when replaying a session.
 *
 * Never blocks the caller, never throws. If the backend is down, the call
 * silently drops.
 */

import { getCurrentUserId } from "./api";

type Fields = Record<string, unknown>;

export function logEvent(kind: string, fields: Fields = {}): void {
  if (typeof window === "undefined") return;
  const userId = getCurrentUserId();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (userId) headers["X-User-Id"] = userId;
  // Fire-and-forget. keepalive lets the request finish if the user navigates
  // away mid-flight, so onUnload events still land.
  try {
    fetch("/api/events", {
      method: "POST",
      headers,
      body: JSON.stringify({
        kind,
        fields: {
          ...fields,
          client_ts: new Date().toISOString(),
          path: window.location.pathname,
        },
      }),
      keepalive: true,
    }).catch(() => {
      /* swallow — observability must never break the UI */
    });
  } catch {
    /* ignore */
  }
}
