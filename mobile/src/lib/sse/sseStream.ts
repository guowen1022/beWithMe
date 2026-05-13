// SSE reader for React Native. We use XMLHttpRequest with `onprogress` rather
// than `fetch().then(res => res.body.getReader())` because RN 0.81's fetch
// doesn't expose `res.body` as a ReadableStream — for finite streams the
// reader sees null and the caller can fall back to `res.text()`, but for an
// infinite stream like /api/dynamic/stream that fallback hangs forever.
// XHR's `responseText` accumulates the body and `onprogress` fires as bytes
// arrive, which gives us live SSE delivery on RN.

import { url, authHeaders, deviceHeaders, UnknownUserError, throwIfUnknownUser } from "../api/client";

export interface StreamSseOptions {
  path: string;
  method?: "GET" | "POST";
  body?: string;
  signal?: AbortSignal;
}

export async function streamSse<TEvent>(
  opts: StreamSseOptions,
  onEvent: (event: TEvent) => void,
): Promise<void> {
  const { path, method = "GET", body, signal } = opts;
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url(path), true);
    const headers: Record<string, string> = { ...authHeaders(), ...deviceHeaders() };
    if (method === "GET") {
      delete headers["Content-Type"];
    }
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.setRequestHeader("Accept", "text/event-stream");

    let cursor = 0;
    let buffer = "";
    let unauthorized = false;

    const flush = () => {
      const text = xhr.responseText;
      if (text.length <= cursor) return;
      buffer += text.slice(cursor);
      cursor = text.length;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) emitLine(line, onEvent);
    };

    xhr.onreadystatechange = () => {
      // 2 = HEADERS_RECEIVED. Check status here so we reject before any body.
      if (xhr.readyState === 2 && xhr.status === 401) {
        unauthorized = true;
      }
    };

    xhr.onprogress = () => { flush(); };

    xhr.onerror = () => {
      reject(new Error(`streamSse network error (${path})`));
    };

    xhr.onload = () => {
      flush();
      // Drain any final trailing line without a newline.
      if (buffer) { emitLine(buffer, onEvent); buffer = ""; }
      if (unauthorized) {
        // Mirror api() — clear the user id so the app reopens onboarding.
        throwIfUnknownUser(new Response(null, { status: 401 })).catch(() => {});
        reject(new UnknownUserError());
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`streamSse failed (${xhr.status} ${path})`));
        return;
      }
      resolve();
    };

    xhr.onabort = () => resolve();

    if (signal) {
      if (signal.aborted) { xhr.abort(); return; }
      signal.addEventListener("abort", () => { try { xhr.abort(); } catch {} }, { once: true });
    }

    xhr.send(body ?? null);
  });
}

function emitLine<TEvent>(line: string, onEvent: (event: TEvent) => void): void {
  if (!line.startsWith("data: ")) return;
  try {
    onEvent(JSON.parse(line.slice(6)) as TEvent);
  } catch {
    // skip malformed
  }
}

// Back-compat shim — keeps the old fetch+reader API for code that hasn't been
// migrated. The Response-based path is unreliable in RN; new callers should
// use streamSse() above.
export async function readSse<TEvent = unknown>(
  res: Response,
  onEvent: (event: TEvent) => void,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  if (opts.signal?.aborted) return;
  const text = await res.text();
  for (const line of text.split("\n")) emitLine(line, onEvent);
}
