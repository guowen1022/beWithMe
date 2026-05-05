"use client";

import { useEffect, useMemo, useRef } from "react";
import { bus } from "@/lib/bus";
import { evalBlockSource, normalizeStyle, reportBlockError } from "@/lib/dynamic";
import { postBlockState, type BlockStateInput, type BlockFocus } from "@/lib/blockState";
import { focusTracker } from "@/lib/focusTracker";
import {
  buildBackendHelpers,
  type BackendArgs,
  type BackendResult,
  type TemplateManifest,
} from "@/lib/templateManifest";

type Props = { id: string; source: string };

const SNAPSHOT_DEBOUNCE_MS = 200;
const SNAPSHOT_MAX_CHARS = 1000;

export function Block({ id, source }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const result = useMemo(() => evalBlockSource(source), [source]);

  useEffect(() => {
    if (!result.ok) {
      reportBlockError(id, result.error);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const block = result.block;
    if (typeof block.content === "string") el.textContent = block.content;

    const cleanups: Array<() => void> = [];

    // ---- Perception reporting (P5/P6) -----------------------------------
    // The block's own helpers.reportState({...}) wins; the MutationObserver
    // fallback below only fires when the block hasn't reported recently.
    let lastRichReportAt = 0;
    let snapshotTimer: ReturnType<typeof setTimeout> | null = null;

    // Resolve the template manifest if the block declared one. The
    // mount-template endpoint stuffs `manifest: {...}` onto the block
    // source object so we can build helpers.backend without a separate
    // round-trip.
    const manifest = (block as { manifest?: TemplateManifest }).manifest;
    const backend = buildBackendHelpers(manifest);

    interface BlockHelpers {
      reportState(state: BlockStateInput): void;
      backend: Record<string, (args?: BackendArgs) => Promise<BackendResult>>;
      blockId: string;
    }
    const helpers: BlockHelpers = {
      reportState(state: BlockStateInput) {
        lastRichReportAt = Date.now();
        // Inject current focus if the block didn't supply one.
        postBlockState(id, {
          ...state,
          focus: state.focus ?? focusTracker.get(id),
        });
      },
      backend,
      blockId: id,
    };

    try {
      // Pass helpers as a 4th arg. Existing blocks that ignore extra args
      // remain unaffected; new blocks can call helpers.reportState(...).
      // The block's run signature in @/lib/dynamic only declares 3 args, so
      // we widen via unknown to satisfy TS without lying about variance.
      const run = block.run as unknown as (
        root: HTMLElement,
        bus: typeof import("@/lib/bus").bus,
        cleanup: (cb: () => void) => void,
        helpers: BlockHelpers,
      ) => void;
      run(el, bus, (cb) => cleanups.push(cb), helpers);
    } catch (err) {
      const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      console.error(`[block ${id}] run error`, err);
      reportBlockError(id, msg);
    }

    // Default snapshot: MutationObserver + trailing-edge debounce. Skipped
    // entirely if the block opts out via `autosnapshot: false`.
    const autosnap = (block as { autosnapshot?: boolean }).autosnapshot !== false;
    if (autosnap) {
      const trySnapshot = () => {
        // If a richer report landed within the same window, skip.
        if (Date.now() - lastRichReportAt < SNAPSHOT_DEBOUNCE_MS) return;
        const text = (el.innerText ?? "").trim().slice(0, SNAPSHOT_MAX_CHARS);
        postBlockState(id, {
          kind: "snapshot",
          content: text,
          focus: focusTracker.get(id),
        });
      };

      const mo = new MutationObserver(() => {
        if (snapshotTimer) clearTimeout(snapshotTimer);
        snapshotTimer = setTimeout(trySnapshot, SNAPSHOT_DEBOUNCE_MS);
      });
      mo.observe(el, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
      });
      cleanups.push(() => {
        mo.disconnect();
        if (snapshotTimer) clearTimeout(snapshotTimer);
      });

      // Initial snapshot — block content set above plus block.run() may
      // have populated DOM synchronously before the observer arms.
      snapshotTimer = setTimeout(trySnapshot, SNAPSHOT_DEBOUNCE_MS);
    }

    // Focus tracker: subscribe so a focus-only change still pushes state
    // (MutationObserver won't fire on hover). Element observation is
    // idempotent.
    //
    // Important: blocks that opt out of auto-snapshot (autosnapshot:false)
    // do so because they emit STRUCTURED reports via helpers.reportState.
    // Sending a kind="snapshot" with raw innerText on every focus change
    // would clobber those structured reports — the perception cache only
    // keeps the latest write per (device, block). Skip the snapshot post
    // for autosnap-disabled blocks; just observe the focus state.
    focusTracker.observeElement(id);
    if (autosnap) {
      const focusUnsub = focusTracker.subscribe(id, (focus: BlockFocus) => {
        const text = (el.innerText ?? "").trim().slice(0, SNAPSHOT_MAX_CHARS);
        postBlockState(id, {
          kind: "snapshot",
          content: text,
          focus,
        });
      });
      cleanups.push(focusUnsub);
    }
    // ---------------------------------------------------------------------

    return () => {
      cleanups.forEach((cb) => { try { cb(); } catch (e) { console.error("[block cleanup]", e); } });
      if (el) el.innerHTML = "";
    };
  }, [id, result]);

  if (!result.ok) {
    return (
      <div
        data-block-id={id}
        style={{
          gridColumn: "1 / span 40",
          gridRow: "1 / span 5",
          color: "#ff8080",
          fontFamily: "ui-monospace, monospace",
          fontSize: 12,
          padding: 8,
        }}
      >
        ⚠ block &quot;{id}&quot;: {result.error}
      </div>
    );
  }

  const block = result.block;
  const isOverlay = block.layer === "overlay";
  const userZ = typeof block.z === "number" ? block.z : 0;
  const gridStyle: React.CSSProperties = {
    gridColumn: `${block.grid.x + 1} / span ${block.grid.w}`,
    gridRow: `${block.grid.y + 1} / span ${block.grid.h}`,
    overflow: "hidden",
    position: "relative",
    zIndex: (isOverlay ? 100 : 0) + userZ,
  };

  return (
    <div
      ref={ref}
      data-block-id={id}
      style={{ ...gridStyle, ...(block.style ? normalizeStyle(block.style) : {}) }}
    />
  );
}
