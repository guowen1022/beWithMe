// Dynamic-canvas block registry. Looks up blocks by `data-block-id` in the
// live DOM and exposes the same handles the reader-side BlockRegistry does:
// scrollIntoView, highlight, focus.
//
// We deliberately avoid a React context here. Canvas blocks live at top
// level and re-render frequently; querying the DOM at action-time is
// simpler than maintaining a parallel handles map. Keep it stateless.

export type DynamicBlockAction = "highlight" | "focus" | "scroll_to" | "raise";

const FLASH_CLASS = "block-flash";
const DEFAULT_HIGHLIGHT_MS = 1600;

// Monotonically increasing z-index counter. Every `raise` bumps the
// target above whatever was raised most recently. Starts at 200 so it
// dominates the static `(isOverlay ? 100 : 0) + userZ` from Block.tsx
// without needing to know what the per-block userZ is.
let _topZ = 200;

function findElement(blockId: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  // CSS.escape isn't ubiquitous in older test envs; fall back to a manual
  // selector concat — block ids are kebab-case ([a-z0-9-]) per the
  // engineer's workspace constraint, so escaping is unnecessary in
  // practice. Be defensive anyway.
  const safe = (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(blockId) : blockId);
  return document.querySelector<HTMLElement>(
    `[data-dynamic-surface] [data-block-id="${safe}"]`,
  );
}

function findScrollableAncestor(el: HTMLElement): HTMLElement | null {
  let cur: HTMLElement | null = el.parentElement;
  while (cur) {
    const style = getComputedStyle(cur);
    const oy = style.overflowY;
    if ((oy === "auto" || oy === "scroll") && cur.scrollHeight > cur.clientHeight) {
      return cur;
    }
    cur = cur.parentElement;
  }
  return null;
}

export const dynamicBlockRegistry = {
  has(blockId: string): boolean {
    return findElement(blockId) !== null;
  },

  scrollTo(blockId: string, opts?: { behavior?: ScrollBehavior }): boolean {
    const el = findElement(blockId);
    if (!el) return false;
    const scroller = findScrollableAncestor(el);
    if (scroller) {
      const top =
        el.getBoundingClientRect().top -
        scroller.getBoundingClientRect().top +
        scroller.scrollTop -
        16;
      scroller.scrollTo({ top, behavior: opts?.behavior ?? "smooth" });
    } else {
      el.scrollIntoView({ behavior: opts?.behavior ?? "smooth", block: "start" });
    }
    return true;
  },

  highlight(blockId: string, durationMs: number = DEFAULT_HIGHLIGHT_MS): boolean {
    const el = findElement(blockId);
    if (!el) return false;
    el.classList.remove(FLASH_CLASS);
    // Force reflow so the animation restarts on rapid repeats.
    void el.offsetWidth;
    el.classList.add(FLASH_CLASS);
    window.setTimeout(() => el.classList.remove(FLASH_CLASS), durationMs);
    return true;
  },

  focus(blockId: string): boolean {
    const el = findElement(blockId);
    if (!el) return false;
    if (el.tabIndex < 0) el.tabIndex = -1;
    el.focus({ preventScroll: true });
    return true;
  },

  /**
   * Bring the block to the front of the stacking order. Sets an inline
   * z-index above all previously-raised blocks; subsequent raises bump
   * higher still. Inline style overrides the static grid-style zIndex
   * computed in Block.tsx, so a raised block always wins regardless of
   * its declared `z` or `layer === "overlay"`.
   */
  raise(blockId: string): boolean {
    const el = findElement(blockId);
    if (!el) return false;
    el.style.zIndex = String(++_topZ);
    return true;
  },

  apply(action: DynamicBlockAction, blockId: string, options?: Record<string, unknown>): boolean {
    switch (action) {
      case "scroll_to":
        return this.scrollTo(blockId, { behavior: options?.behavior as ScrollBehavior | undefined });
      case "highlight":
        return this.highlight(blockId, (options?.durationMs as number | undefined) ?? DEFAULT_HIGHLIGHT_MS);
      case "focus":
        return this.focus(blockId);
      case "raise":
        return this.raise(blockId);
      default:
        return false;
    }
  },
};

// Click-to-raise: a single delegated listener on document picks any
// click inside `[data-dynamic-surface] [data-block-id="..."]` and
// raises that block. One listener for the whole surface, no per-block
// boilerplate, no React re-renders. Idempotent — module loads once.
if (typeof document !== "undefined") {
  document.addEventListener(
    "mousedown",
    (e) => {
      const target = e.target as Element | null;
      if (!target) return;
      const el = target.closest?.("[data-dynamic-surface] [data-block-id]") as HTMLElement | null;
      if (!el) return;
      const id = el.getAttribute("data-block-id");
      if (id) dynamicBlockRegistry.raise(id);
    },
    // Use capture so the raise happens BEFORE the block's own click
    // handlers see the event — keeps z-index stable while interacting
    // with controls inside the block.
    true,
  );
}

if (typeof window !== "undefined") {
  (window as unknown as { __dynamicBlockRegistry?: typeof dynamicBlockRegistry }).__dynamicBlockRegistry =
    dynamicBlockRegistry;
}
