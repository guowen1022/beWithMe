"use client";

/**
 * Block registry — a single addressing scheme for every scrollable thing in
 * the UI: answer logic blocks, parent-card blocks, PDF pages, etc. Each
 * block exposes a uniform handle (scroll, highlight, focus) so callers
 * outside the component (other components, console, future teacher
 * tool-calls) can drive them by id.
 *
 * ID convention:
 *   answer:<questionLocalId>:block-N
 *   parent:<questionLocalId>:block-N
 *   pdf:page-N
 *   passage:para-N (reserved)
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
} from "react";

export type BlockKind = "answer" | "parent" | "pdf-page" | "passage";

export interface BlockHandle {
  id: string;
  kind: BlockKind;
  /** Bring this block into view inside its scrollable ancestor. */
  scrollIntoView: (opts?: { behavior?: ScrollBehavior }) => void;
  /** Flash an attention ring around the block for `durationMs`. */
  highlight: (durationMs?: number) => void;
  /** Optional: move keyboard focus to the block. */
  focus?: () => void;
  getElement: () => HTMLElement | null;
}

interface RegistryShape {
  register: (handle: BlockHandle) => () => void;
  get: (id: string) => BlockHandle | undefined;
  list: (kind?: BlockKind) => BlockHandle[];
  scrollIntoView: (id: string, opts?: { behavior?: ScrollBehavior }) => boolean;
  highlight: (id: string, durationMs?: number) => boolean;
  focus: (id: string) => boolean;
}

const BlockRegistryContext = createContext<RegistryShape | null>(null);

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

export function BlockRegistryProvider({ children }: { children: React.ReactNode }) {
  const handlesRef = useRef<Map<string, BlockHandle>>(new Map());

  const register = useCallback((handle: BlockHandle) => {
    handlesRef.current.set(handle.id, handle);
    return () => {
      const cur = handlesRef.current.get(handle.id);
      if (cur === handle) handlesRef.current.delete(handle.id);
    };
  }, []);

  const get = useCallback((id: string) => handlesRef.current.get(id), []);

  const list = useCallback((kind?: BlockKind) => {
    const all = Array.from(handlesRef.current.values());
    return kind ? all.filter((h) => h.kind === kind) : all;
  }, []);

  const scrollIntoView = useCallback(
    (id: string, opts?: { behavior?: ScrollBehavior }) => {
      const h = handlesRef.current.get(id);
      if (!h) return false;
      h.scrollIntoView(opts);
      return true;
    },
    [],
  );

  const highlight = useCallback((id: string, durationMs?: number) => {
    const h = handlesRef.current.get(id);
    if (!h) return false;
    h.highlight(durationMs);
    return true;
  }, []);

  const focus = useCallback((id: string) => {
    const h = handlesRef.current.get(id);
    if (!h?.focus) return false;
    h.focus();
    return true;
  }, []);

  const value = useMemo<RegistryShape>(
    () => ({ register, get, list, scrollIntoView, highlight, focus }),
    [register, get, list, scrollIntoView, highlight, focus],
  );

  // Expose to window for console debugging and (eventually) external
  // tool-call drivers. Read-only: external code can call methods but
  // can't mutate the map directly.
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as unknown as { __blockRegistry?: RegistryShape }).__blockRegistry = value;
    return () => {
      delete (window as unknown as { __blockRegistry?: RegistryShape }).__blockRegistry;
    };
  }, [value]);

  return (
    <BlockRegistryContext.Provider value={value}>
      {children}
    </BlockRegistryContext.Provider>
  );
}

export function useBlockRegistry(): RegistryShape {
  const ctx = useContext(BlockRegistryContext);
  if (!ctx) {
    throw new Error("useBlockRegistry must be used inside <BlockRegistryProvider>");
  }
  return ctx;
}

/**
 * Register a DOM element as a block. The default scrollIntoView walks up to
 * the nearest scrollable ancestor (overflow:auto/scroll with overflow), so
 * blocks inside drawers, panes, or PDF containers all just work.
 *
 * `highlight` toggles the .block-flash class for `durationMs`.
 */
export function useRegisterBlock(opts: {
  id: string;
  kind: BlockKind;
  ref: React.RefObject<HTMLElement | null>;
  enabled?: boolean;
}) {
  const { id, kind, ref, enabled = true } = opts;
  const registry = useBlockRegistry();

  useEffect(() => {
    if (!enabled) return;
    const handle: BlockHandle = {
      id,
      kind,
      getElement: () => ref.current,
      scrollIntoView: (o) => {
        const el = ref.current;
        if (!el) return;
        const scroller = findScrollableAncestor(el);
        if (scroller) {
          const top =
            el.getBoundingClientRect().top -
            scroller.getBoundingClientRect().top +
            scroller.scrollTop -
            16;
          scroller.scrollTo({ top, behavior: o?.behavior ?? "smooth" });
        } else {
          el.scrollIntoView({ behavior: o?.behavior ?? "smooth", block: "start" });
        }
      },
      highlight: (durationMs = 1600) => {
        const el = ref.current;
        if (!el) return;
        el.classList.remove("block-flash");
        // force reflow so the animation restarts when called twice in a row
        void el.offsetWidth;
        el.classList.add("block-flash");
        window.setTimeout(() => {
          el.classList.remove("block-flash");
        }, durationMs);
      },
      focus: () => {
        const el = ref.current;
        if (!el) return;
        if (el.tabIndex < 0) el.tabIndex = -1;
        el.focus({ preventScroll: true });
      },
    };
    return registry.register(handle);
  }, [id, kind, ref, registry, enabled]);
}
