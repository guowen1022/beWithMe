// Per-block helpers. Mirrors frontend/components/Block.tsx:makeBackendCaller
// and the helpers builder. Each block instance gets:
//   - backend.<callName>(args) — typed fetch wrapper built from the manifest
//   - audio.{startVad, transcribe, ...} — audio primitives (Phase 1: partial)
//   - bus — shared pub/sub (sticky)
//   - reportState(state) — perception reporting (Phase 2)
//   - blockId

import { bus, type Bus } from "../lib/bus/bus";
import { api, url } from "../lib/api/client";
import type { BackendCallSpec, BackendCaller, BackendResult, BlockManifest } from "./blockRegistry";

export interface BlockAudio {
  transcribe: (wav: Uint8Array, language?: string) => Promise<{ text: string; duration_seconds: number }>;
}

export interface BlockHelpers {
  blockId: string;
  bus: Bus;
  backend: Record<string, BackendCaller>;
  audio: BlockAudio;
  reportState: (state: Record<string, unknown>) => void;
}

function makeBackendCaller(spec: BackendCallSpec): BackendCaller {
  return async (args?: Record<string, unknown> | FormData): Promise<BackendResult> => {
    const isMultipart = spec.content_type === "multipart/form-data";
    const isGet = spec.method === "GET";
    const res = await api(spec.path, {
      method: spec.method,
      body: isGet ? undefined : (isMultipart ? (args as FormData) : JSON.stringify(args ?? {})),
      multipart: isMultipart,
    }).catch(async () => {
      // api() resolves the response — only network errors throw here.
      return new Response(null, { status: 0 });
    });

    let data: unknown = null;
    if (spec.returns === "text") data = await res.text().catch(() => "");
    else if (spec.returns === "blob") data = await res.blob().catch(() => null);
    else data = await res.json().catch(() => null);

    return { ok: res.ok, status: res.status, data };
  };
}

export function buildHelpers(blockId: string, manifest: BlockManifest): BlockHelpers {
  const backend: BlockHelpers["backend"] = {};
  for (const [name, spec] of Object.entries(manifest.backend)) {
    backend[name] = makeBackendCaller(spec);
  }

  return {
    blockId,
    bus,
    backend,
    audio: {
      transcribe: async (wav, language = "en") => {
        const fd = new FormData();
        fd.append("file", new Blob([wav as BlobPart], { type: "audio/wav" }), "audio.wav");
        fd.append("language", language);
        const res = await api("/api/transcribe", { method: "POST", body: fd, multipart: true });
        if (!res.ok) throw new Error(`transcribe failed (${res.status})`);
        return res.json();
      },
    },
    reportState: (_state) => {
      // Phase 2: POST to /api/perception/snapshot. Phase 1 no-op.
    },
  };
}

// Re-export for convenience; the helpers themselves don't need url() but
// the AmbientMicBlock may want it for diagnostics.
export { url };
