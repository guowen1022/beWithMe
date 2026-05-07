// Visibility registry for built-in "system" blocks (the command bar,
// teacher-thinking panel, ...). They live on the same grid as
// dynamic source-eval blocks and are draggable through the same shell,
// but their content is React, not evaluated source. Their ids are
// prefixed `system:` so the existing teacher SSE protocol — which sends
// `ui-update {action: "unmount"|"mount", block:{id}}` — can hide and
// re-show them without any new wire format.

const STORAGE_KEY = "bewithme_system_blocks_v1";
export const SYSTEM_BLOCK_PREFIX = "system:";

type Listener = () => void;

type Stored = { hidden: string[] };

class SystemBlocksStore {
  private hidden = new Set<string>();
  private listeners = new Set<Listener>();
  private hydrated = false;

  private hydrate(): void {
    if (this.hydrated || typeof window === "undefined") return;
    this.hydrated = true;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Stored;
      if (parsed && Array.isArray(parsed.hidden)) {
        for (const id of parsed.hidden) {
          if (typeof id === "string") this.hidden.add(id);
        }
      }
    } catch (e) {
      console.warn("[systemBlocks] hydrate failed", e);
    }
  }

  private persist(): void {
    if (typeof window === "undefined") return;
    try {
      const data: Stored = { hidden: Array.from(this.hidden) };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch (e) {
      console.warn("[systemBlocks] persist failed", e);
    }
  }

  isHidden(id: string): boolean {
    this.hydrate();
    return this.hidden.has(id);
  }

  hide(id: string): void {
    this.hydrate();
    if (this.hidden.has(id)) return;
    this.hidden.add(id);
    this.persist();
    this.fire();
  }

  show(id: string): void {
    this.hydrate();
    if (!this.hidden.delete(id)) return;
    this.persist();
    this.fire();
  }

  toggle(id: string): void {
    if (this.isHidden(id)) this.show(id);
    else this.hide(id);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private fire(): void {
    for (const l of Array.from(this.listeners)) {
      try { l(); } catch (e) { console.error("[systemBlocks]", e); }
    }
  }
}

export const systemBlocks = new SystemBlocksStore();

export function isSystemBlockId(id: string): boolean {
  return id.startsWith(SYSTEM_BLOCK_PREFIX);
}

if (typeof window !== "undefined") {
  (window as unknown as { __systemBlocks?: SystemBlocksStore }).__systemBlocks = systemBlocks;
}
