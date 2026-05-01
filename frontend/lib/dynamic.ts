// Dynamic block runtime — eval JS source, mount, run lifecycle, route bus
// publishes. The browser is the sandbox: an eval failure POSTs the error
// back to /api/dynamic/error/{block_id} and renders a fallback chip.

export type BlockGrid = { x: number; y: number; w: number; h: number };

export type EvaluatedBlock = {
  id: string;
  grid: BlockGrid;
  content?: string;
  style?: Record<string, string | number>;
  layer?: "canvas" | "overlay";
  z?: number;
  subscribes?: string[];
  publishes?: string[];
  run: (root: HTMLDivElement, bus: unknown, cleanup: (cb: () => void) => void) => void;
};

export type EvalResult = { ok: true; block: EvaluatedBlock } | { ok: false; error: string };

export function evalBlockSource(source: string): EvalResult {
  try {
    const fn = new Function(`"use strict"; return (${source});`);
    const obj = fn();
    if (!obj || typeof obj !== "object") {
      return { ok: false, error: "source did not evaluate to an object" };
    }
    if (typeof (obj as { run?: unknown }).run !== "function") {
      return { ok: false, error: "block.run is not a function" };
    }
    if (typeof (obj as { id?: unknown }).id !== "string") {
      return { ok: false, error: "block.id is not a string" };
    }
    return { ok: true, block: obj as EvaluatedBlock };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? `${e.name}: ${e.message}` : String(e) };
  }
}

// Style keys arrive in either camelCase or kebab-case (the PoC blocks
// sometimes use one, sometimes the other). React expects camelCase, so
// normalize.
export function normalizeStyle(style: Record<string, string | number>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(style)) {
    out[k.includes("-") ? k.replace(/-([a-z])/g, (_, c) => c.toUpperCase()) : k] = v;
  }
  return out;
}

// Per-block source registry. The DynamicSurface listens to the SSE stream
// and updates this map; React re-renders consumers via the `subscribe`
// hook below.
export type SourceEntry = { id: string; source: string };

class SourceRegistry {
  private entries = new Map<string, SourceEntry>();
  private listeners = new Set<() => void>();
  private snap: Readonly<SourceEntry[]> = Object.freeze([]);

  private rebuildSnapshot() {
    this.snap = Object.freeze(
      Array.from(this.entries.values()).sort((a, b) => a.id.localeCompare(b.id)),
    );
  }

  mount(entry: SourceEntry) {
    this.entries.set(entry.id, entry);
    this.rebuildSnapshot();
    this.fire();
  }

  unmount(id: string) {
    if (!this.entries.delete(id)) return;
    this.rebuildSnapshot();
    this.fire();
  }

  has(id: string): boolean {
    return this.entries.has(id);
  }

  list(): Readonly<SourceEntry[]> {
    return this.snap;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private fire() {
    this.listeners.forEach((l) => { try { l(); } catch (e) { console.error("[registry]", e); } });
  }
}

export const sourceRegistry = new SourceRegistry();

if (typeof window !== "undefined") {
  (window as unknown as { __blockRegistry: SourceRegistry }).__blockRegistry = sourceRegistry;
}

// Helper used by the SSE handler in DynamicSurface to report eval failures.
export async function reportBlockError(blockId: string, error: string): Promise<void> {
  try {
    const userId = typeof window !== "undefined" ? localStorage.getItem("bewithme_user_id") : null;
    await fetch(`/api/dynamic/error/${encodeURIComponent(blockId)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify({ error }),
    });
  } catch (e) {
    console.error("[dynamic] failed to report block error", e);
  }
}
