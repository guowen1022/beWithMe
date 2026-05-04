// Template manifest — the contract between a block template's metadata
// and the helpers.backend.<name>(args) resolver in Block.tsx.
//
// The shape we want every template to declare in its `.md` frontmatter:
//
//   ---
//   keywords: upload file paper document pdf
//   backend:
//     upload:
//       method: POST
//       path: /api/documents/upload
//       auth: user                    # auto-inject X-User-Id
//       content_type: multipart/form-data
//       returns: json                 # | "stream" | "blob"
//   ---
//
// The Python template loader on the backend (engineer agent) parses the
// same convention so when a template is mounted via /dynamic/mount-template
// we can carry its manifest along to the frontend.
//
// On the frontend, the manifest gets attached to the rendered block as
// `data-template-manifest` (JSON), Block.tsx reads it on mount, and builds
// `helpers.backend` so block code is `helpers.backend.upload(formData)`
// rather than `fetch('/api/documents/upload', ...)`.

import { deviceHeaders } from "./deviceId";
import { getCurrentUserId } from "./api";

export type BackendReturnKind = "json" | "stream" | "blob" | "text";

export interface BackendCallSpec {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  auth?: "user" | "none";              // default: "user"
  content_type?: "application/json" | "multipart/form-data" | "text/plain";
  returns?: BackendReturnKind;          // default: "json"
}

export interface TemplateManifest {
  /** Map of backend call name → spec. */
  backend?: Record<string, BackendCallSpec>;
  /** Bus topics this template publishes to. */
  publishes?: string[];
  /** Bus topics this template subscribes to. */
  subscribes?: string[];
}

export type BackendArgs =
  | FormData
  | Record<string, unknown>
  | undefined;

export interface BackendResult {
  ok: boolean;
  status: number;
  data: unknown;        // parsed per spec.returns
}

function buildHeaders(spec: BackendCallSpec, hasFormData: boolean): Record<string, string> {
  const headers: Record<string, string> = { ...deviceHeaders() };
  if (spec.auth !== "none") {
    const uid = getCurrentUserId();
    if (uid) headers["X-User-Id"] = uid;
  }
  if (!hasFormData) {
    headers["Content-Type"] = spec.content_type ?? "application/json";
  }
  return headers;
}

function buildBody(spec: BackendCallSpec, args: BackendArgs): BodyInit | undefined {
  if (args === undefined) return undefined;
  if (args instanceof FormData) return args;
  if (spec.content_type === "text/plain") return String(args);
  return JSON.stringify(args);
}

/**
 * Build a fetch wrapper for one named backend call. Auto-injects auth +
 * device headers; serializes args; parses the response per spec.returns.
 */
export function makeBackendCaller(spec: BackendCallSpec) {
  return async (args?: BackendArgs): Promise<BackendResult> => {
    const isForm = args instanceof FormData;
    const init: RequestInit = {
      method: spec.method,
      headers: buildHeaders(spec, isForm),
    };
    if (spec.method !== "GET" && args !== undefined) {
      init.body = buildBody(spec, args);
    }
    const res = await fetch(spec.path, init);

    let data: unknown = null;
    const kind = spec.returns ?? "json";
    if (kind === "json") {
      try { data = await res.json(); } catch { data = null; }
    } else if (kind === "text") {
      data = await res.text();
    } else if (kind === "blob") {
      data = await res.blob();
    } else if (kind === "stream") {
      data = res.body;
    }
    return { ok: res.ok, status: res.status, data };
  };
}

/**
 * Build the helpers.backend object for one block. The keys are the names
 * declared in the manifest's `backend` map; the values are async callers.
 */
export function buildBackendHelpers(
  manifest: TemplateManifest | undefined,
): Record<string, (args?: BackendArgs) => Promise<BackendResult>> {
  const out: Record<string, (args?: BackendArgs) => Promise<BackendResult>> = {};
  if (!manifest?.backend) return out;
  for (const [name, spec] of Object.entries(manifest.backend)) {
    out[name] = makeBackendCaller(spec);
  }
  return out;
}

/**
 * Parse a manifest from a serialized JSON string (as carried by the
 * `data-template-manifest` attribute, or sourced from the canvas
 * hydration response). Tolerates undefined/empty.
 */
export function parseManifest(raw: string | null | undefined): TemplateManifest | undefined {
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw) as TemplateManifest;
    return parsed;
  } catch {
    return undefined;
  }
}
