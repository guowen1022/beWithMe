// Focus tracker — single shared module so blocks don't each re-implement
// "is this block currently the user's attention?" detection.
//
// Watches three signals at the document level:
//   - mouseover / mouseout on [data-block-id] ancestors
//       → most recently hovered block becomes `active`
//   - focusin / focusout on [data-block-id] ancestors
//       → keyboard focus promotes a block to `active`, demotes the previous one
//   - IntersectionObserver on every [data-block-id]
//       → ratio == 0 → `background`
//       → ratio  > 0 → `visible` (unless already `active`)
//
// Invariant: at most one block is `active` at a time. Promoting one demotes
// whatever was previously active, which fires its own change notification.
//
// The tracker is purely client-side bookkeeping. Block.tsx subscribes to
// changes and pushes the new focus value into the perception cache via
// postBlockState. The backend cache enforces the same invariant per device.

export type BlockFocus = "active" | "visible" | "background";

type Listener = (focus: BlockFocus) => void;

interface Entry {
  focus: BlockFocus;
  listeners: Set<Listener>;
  visible: boolean;   // updated by IntersectionObserver
}

class FocusTracker {
  private entries = new Map<string, Entry>();
  private active: string | null = null;     // currently `active` block id
  private io: IntersectionObserver | null = null;
  private hookedDom = false;

  ensure(blockId: string): Entry {
    let entry = this.entries.get(blockId);
    if (!entry) {
      entry = { focus: "background", listeners: new Set(), visible: false };
      this.entries.set(blockId, entry);
    }
    return entry;
  }

  get(blockId: string): BlockFocus {
    return this.entries.get(blockId)?.focus ?? "background";
  }

  subscribe(blockId: string, listener: Listener): () => void {
    this.hookDomOnce();
    const entry = this.ensure(blockId);
    entry.listeners.add(listener);
    // Also start observing this block's element if we can find it now;
    // observeElement is idempotent so a later mount picks it up too.
    this.observeElement(blockId);
    return () => {
      const e = this.entries.get(blockId);
      if (!e) return;
      e.listeners.delete(listener);
      if (e.listeners.size === 0 && this.active !== blockId) {
        this.entries.delete(blockId);
      }
    };
  }

  /** Called by Block.tsx when a block's root mounts. Idempotent. */
  observeElement(blockId: string): void {
    if (typeof document === "undefined") return;
    if (!this.io) {
      this.io = new IntersectionObserver(
        (entries) => {
          for (const e of entries) {
            const id = (e.target as HTMLElement).getAttribute("data-block-id");
            if (!id) continue;
            const entry = this.ensure(id);
            const wasVisible = entry.visible;
            entry.visible = e.intersectionRatio > 0;
            if (entry.visible !== wasVisible) {
              // visibility flip — recompute focus
              this.recomputeFocus(id);
            }
          }
        },
        { threshold: [0, 0.01] },
      );
    }
    const els = document.querySelectorAll(`[data-block-id="${cssEscape(blockId)}"]`);
    els.forEach((el) => this.io!.observe(el));
  }

  private hookDomOnce(): void {
    if (this.hookedDom || typeof document === "undefined") return;
    this.hookedDom = true;
    document.addEventListener("mouseover", (e) => this.onMouse(e, true), { capture: true });
    document.addEventListener("mouseout", (e) => this.onMouse(e, false), { capture: true });
    document.addEventListener("focusin", (e) => this.onFocus(e, true), { capture: true });
    document.addEventListener("focusout", (e) => this.onFocus(e, false), { capture: true });
  }

  private onMouse(e: Event, entering: boolean): void {
    const blockId = nearestBlockId(e.target as Element | null);
    if (!blockId) return;
    if (entering) {
      this.promote(blockId);
    } else {
      // Mouseout doesn't automatically demote — keyboard focus or another
      // mouseover decides. If nothing else holds attention we'll fall back
      // to visible on the next pointer move.
    }
  }

  private onFocus(e: Event, entering: boolean): void {
    const blockId = nearestBlockId(e.target as Element | null);
    if (!blockId) return;
    if (entering) {
      this.promote(blockId);
    } else {
      // focusout can fire as part of a focus-shift; the corresponding
      // focusin on the new block (if any) will promote it. If focus
      // simply leaves, demote without picking a new active.
      this.demoteIfActive(blockId);
    }
  }

  private promote(blockId: string): void {
    if (this.active === blockId) return;
    const prevActive = this.active;
    this.active = blockId;
    const entry = this.ensure(blockId);
    if (entry.focus !== "active") {
      entry.focus = "active";
      this.fire(blockId);
    }
    if (prevActive) {
      const prev = this.entries.get(prevActive);
      if (prev) {
        // Recompute previous's focus (visible vs background) without
        // re-firing if unchanged.
        const next = prev.visible ? "visible" : "background";
        if (prev.focus !== next) {
          prev.focus = next;
          this.fire(prevActive);
        }
      }
    }
  }

  private demoteIfActive(blockId: string): void {
    if (this.active !== blockId) return;
    this.active = null;
    const entry = this.entries.get(blockId);
    if (!entry) return;
    const next = entry.visible ? "visible" : "background";
    if (entry.focus !== next) {
      entry.focus = next;
      this.fire(blockId);
    }
  }

  private recomputeFocus(blockId: string): void {
    const entry = this.entries.get(blockId);
    if (!entry) return;
    if (this.active === blockId) {
      // Active stays active even if visibility flips momentarily.
      return;
    }
    const next = entry.visible ? "visible" : "background";
    if (entry.focus !== next) {
      entry.focus = next;
      this.fire(blockId);
    }
  }

  private fire(blockId: string): void {
    const entry = this.entries.get(blockId);
    if (!entry) return;
    for (const listener of Array.from(entry.listeners)) {
      try {
        listener(entry.focus);
      } catch (err) {
        console.warn("[focusTracker] listener threw", err);
      }
    }
  }
}

function nearestBlockId(el: Element | null): string | null {
  let cur: Element | null = el;
  while (cur && cur !== document.documentElement) {
    if (cur instanceof HTMLElement) {
      const id = cur.getAttribute("data-block-id");
      if (id) return id;
    }
    cur = cur.parentElement;
  }
  return null;
}

function cssEscape(s: string): string {
  return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s;
}

export const focusTracker = new FocusTracker();

if (typeof window !== "undefined") {
  (window as unknown as { __focusTracker?: FocusTracker }).__focusTracker = focusTracker;
}
