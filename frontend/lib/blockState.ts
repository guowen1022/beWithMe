// Per-block state reporter.
//
// Blocks (and Block.tsx's auto-snapshot fallback) push their current state
// to the perception cache via POST /api/dynamic/state/{block_id}. The
// persona reads the cache through the read_media tool.
//
// Per-block trailing-edge debounce (~200ms): rapid updates collapse into
// one POST per block per window, with the latest state. Per-block, not
// global, so a chatty block doesn't starve a calm one.
//
// Strict typing for `kind` and `focus` is intentional — the persona
// reasons over these fields and a typo would silently mis-classify a
// block. `extra` is freeform.

import { deviceHeaders } from "./deviceId";
import { getCurrentUserId } from "./api";

export type BlockFocus = "active" | "visible" | "background";

export interface BlockGrid {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface BlockStateInput {
  kind?: string;
  content?: string;
  focus?: BlockFocus;
  /** Set true when the block has finished a discrete unit of work (e.g.
   *  upload completed, form submitted). The backend edge-detects the
   *  false→true transition and triggers the teacher's tool loop. Never
   *  set for blocks that are continuously updating. */
  completed?: boolean;
  /** The block's effective grid on the 160×90 canvas — what the user
   *  actually sees right now. The teacher reads this to decide whether
   *  to call layout_blocks. */
  grid?: BlockGrid;
  extra?: Record<string, unknown>;
}

const DEBOUNCE_MS = 200;
const CONTENT_MAX = 1000;

interface Pending {
  state: BlockStateInput;
  timer: ReturnType<typeof setTimeout> | null;
}
const pending = new Map<string, Pending>();

function flushOne(blockId: string): void {
  const slot = pending.get(blockId);
  if (!slot) return;
  pending.delete(blockId);
  void send(blockId, slot.state);
}

/** Read the block's currently-rendered grid from the DOM. Returns null if
 * the element isn't in the surface yet, or if computed-style values can't
 * be parsed back into integer line numbers (e.g. mid-render race).
 */
function readGridFromDom(blockId: string): BlockGrid | null {
  if (typeof document === "undefined") return null;
  const safe = (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(blockId) : blockId);
  const el = document.querySelector<HTMLElement>(
    `[data-dynamic-surface] [data-block-id="${safe}"]`,
  );
  if (!el) return null;
  const cs = getComputedStyle(el);
  const colStart = parseInt(cs.gridColumnStart, 10);
  const colEnd = parseInt(cs.gridColumnEnd, 10);
  const rowStart = parseInt(cs.gridRowStart, 10);
  const rowEnd = parseInt(cs.gridRowEnd, 10);
  if (
    !Number.isFinite(colStart) || !Number.isFinite(colEnd) ||
    !Number.isFinite(rowStart) || !Number.isFinite(rowEnd) ||
    colEnd <= colStart || rowEnd <= rowStart
  ) {
    return null;
  }
  return {
    x: colStart - 1,
    y: rowStart - 1,
    w: colEnd - colStart,
    h: rowEnd - rowStart,
  };
}

async function send(blockId: string, state: BlockStateInput): Promise<void> {
  const userId = getCurrentUserId();
  if (!userId) return;   // unauthenticated; nothing to report against
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-User-Id": userId,
    ...deviceHeaders(),
  };
  // Always attach the current grid (as rendered) so the teacher prompt's
  // CURRENTLY ON CANVAS section can show layout coordinates. Caller-
  // provided grid wins; otherwise read from the DOM.
  const grid = state.grid ?? readGridFromDom(blockId) ?? undefined;
  const body: Record<string, unknown> = {
    kind: state.kind ?? "snapshot",
    content: (state.content ?? "").slice(0, CONTENT_MAX),
    focus: state.focus ?? "visible",
    completed: state.completed ?? false,
    extra: state.extra ?? {},
  };
  if (grid) body.grid = grid;
  try {
    await fetch(`/api/dynamic/state/${encodeURIComponent(blockId)}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      keepalive: true,   // survive page-unload races
    });
  } catch (err) {
    // Best-effort: state is transient anyway. Log once per failure mode
    // would be nice but in dev a console warn per error is fine.
    console.warn("[blockState] post failed", blockId, err);
  }
}

/**
 * Report a block's current state. Coalesced per-block; the latest call
 * within DEBOUNCE_MS wins.
 */
export function postBlockState(blockId: string, state: BlockStateInput): void {
  if (!blockId) return;
  const existing = pending.get(blockId);
  if (existing && existing.timer) clearTimeout(existing.timer);
  const timer = setTimeout(() => flushOne(blockId), DEBOUNCE_MS);
  pending.set(blockId, { state, timer });
}

/**
 * Force-flush all pending states immediately (e.g. before unmount or on
 * tab close). Synchronous return; the actual POST is fire-and-forget.
 */
export function flushAllBlockStates(): void {
  for (const blockId of Array.from(pending.keys())) {
    const slot = pending.get(blockId);
    if (slot?.timer) clearTimeout(slot.timer);
    flushOne(blockId);
  }
}

if (typeof window !== "undefined") {
  // Best-effort flush on tab close so the persona's last-known view is
  // accurate. fetch keepalive lets the request finish after unload.
  window.addEventListener("pagehide", flushAllBlockStates);
}
