// Per-block layout overrides + drag state.
//
// Block sources declare their grid in desktop coords (12×9). The user can
// drag a block to a new cell on the canvas; that move is recorded here and
// wins over the source's own `grid` until the user clears it. Stored to
// localStorage so a refresh keeps the layout the user arranged.

import type { GridCoords } from "./gridConfig";

const STORAGE_KEY = "bewithme_block_layout_v1";

type Listener = () => void;

class LayoutStore {
  private overrides = new Map<string, GridCoords>();
  private listeners = new Set<Listener>();
  private hydrated = false;

  private hydrate(): void {
    if (this.hydrated || typeof window === "undefined") return;
    this.hydrated = true;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Record<string, GridCoords>;
      for (const [id, coords] of Object.entries(parsed)) {
        if (
          coords && typeof coords === "object" &&
          Number.isFinite(coords.x) && Number.isFinite(coords.y) &&
          Number.isFinite(coords.w) && Number.isFinite(coords.h)
        ) {
          this.overrides.set(id, coords);
        }
      }
    } catch (e) {
      console.warn("[blockLayout] hydrate failed", e);
    }
  }

  private persist(): void {
    if (typeof window === "undefined") return;
    try {
      const obj: Record<string, GridCoords> = {};
      for (const [k, v] of this.overrides) obj[k] = v;
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch (e) {
      console.warn("[blockLayout] persist failed", e);
    }
  }

  get(id: string): GridCoords | null {
    this.hydrate();
    return this.overrides.get(id) ?? null;
  }

  set(id: string, coords: GridCoords): void {
    this.hydrate();
    this.overrides.set(id, coords);
    this.persist();
    this.fire();
  }

  clear(id: string): void {
    if (this.overrides.delete(id)) {
      this.persist();
      this.fire();
    }
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private fire(): void {
    for (const l of Array.from(this.listeners)) {
      try { l(); } catch (e) { console.error("[blockLayout]", e); }
    }
  }
}

export const blockLayout = new LayoutStore();

// ---- Drag state -----------------------------------------------------------

export type DragSnapshot = {
  id: string;
  /** Snap target in desktop coords. Updated as the pointer crosses cell lines. */
  target: GridCoords;
} | null;

class DragController {
  private state: DragSnapshot = null;
  private listeners = new Set<Listener>();

  get(): DragSnapshot { return this.state; }

  start(id: string, target: GridCoords): void {
    this.state = { id, target };
    this.fire();
  }

  update(target: GridCoords): void {
    if (!this.state) return;
    const cur = this.state.target;
    if (target.x === cur.x && target.y === cur.y && target.w === cur.w && target.h === cur.h) {
      return;
    }
    this.state = { id: this.state.id, target };
    this.fire();
  }

  end(): void {
    if (!this.state) return;
    this.state = null;
    this.fire();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private fire(): void {
    for (const l of Array.from(this.listeners)) {
      try { l(); } catch (e) { console.error("[dragController]", e); }
    }
  }
}

export const dragController = new DragController();

if (typeof window !== "undefined") {
  const w = window as unknown as {
    __blockLayout?: LayoutStore;
    __dragController?: DragController;
  };
  w.__blockLayout = blockLayout;
  w.__dragController = dragController;
}
