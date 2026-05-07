"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { Block } from "./Block";
import { BlockShell } from "./BlockShell";
import CanvasCommandBar from "./CanvasCommandBar";
import TeacherThinkingPanel from "./TeacherThinkingPanel";
import { bus } from "@/lib/bus";
import { sourceRegistry, type SourceEntry } from "@/lib/dynamic";
import { useDeviceClass } from "@/lib/device";
import { GRID_SIZES, scaleGridForDevice, type GridCoords } from "@/lib/gridConfig";
import { dragController, type DragSnapshot } from "@/lib/blockLayout";
import { systemBlocks, isSystemBlockId } from "@/lib/systemBlocks";
import { fetchCanvas, mountTemplate, subscribeToDynamicStream } from "@/lib/api";
import { loadPdfjs } from "@/lib/pdfjs-loader";
import { dynamicBlockRegistry } from "@/lib/dynamicBlockRegistry";

// Built-in blocks that share the same draggable grid as source-eval blocks.
// Their ids are namespaced `system:*` so the existing teacher SSE
// `ui-update` protocol can hide and re-show them without a wire change.
type SystemBlockSpec = {
  id: string;
  defaultGrid: GridCoords;
  Component: React.FC;
};

const SYSTEM_BLOCKS: SystemBlockSpec[] = [
  {
    id: "system:teacher-thinking",
    defaultGrid: { x: 9, y: 0, w: 3, h: 3 },
    Component: TeacherThinkingPanel,
  },
  {
    id: "system:command-bar",
    // Bottom strip across the canvas; tall enough that the inline debug
    // panel fits when the user toggles it open.
    defaultGrid: { x: 1, y: 7, w: 10, h: 2 },
    Component: CanvasCommandBar,
  },
];

function useRegistry(): Readonly<SourceEntry[]> {
  return useSyncExternalStore(
    (l) => sourceRegistry.subscribe(l),
    () => sourceRegistry.list(),
    () => sourceRegistry.list(),
  );
}

type Mode = "overlay" | "fullscreen";

type Props = {
  /**
   * "overlay"   — surface is `position: fixed` over whatever else is on the
   *               page; pointer-events pass through gaps so users can still
   *               interact with the underlying UI; surface itself hides when
   *               no blocks are mounted.
   * "fullscreen" — surface IS the page; dark backdrop, always visible (even
   *                with zero blocks so the canvas is recognizable), pointer
   *                events on by default.
   */
  mode?: Mode;
};

export default function DynamicSurface({ mode = "overlay" }: Props) {
  const entries = useRegistry();
  const device = useDeviceClass();

  // Make pdf.js available to vanilla-JS blocks (e.g. the pdf_reader template)
  // by warming `window.pdfjsLib` once. Idempotent across re-mounts.
  useEffect(() => {
    loadPdfjs().catch((e) => console.warn("[dynamic-surface] pdf.js load failed", e));
  }, []);

  // Hydrate the registry from the user's per-user-git workspace on
  // mount. With templates being ephemeral (no workspace writes), the
  // hydrator only sees engineer-novel widgets the user has explicitly
  // saved — empty for new users. When the canvas is empty, mount the
  // `lets_begin` welcome card as the first paint; clicking it transitions
  // the canvas to the ambient_mic block.
  useEffect(() => {
    if (mode !== "fullscreen") return;
    let cancelled = false;
    fetchCanvas()
      .then((blocks) => {
        if (cancelled) return;
        for (const b of blocks) {
          sourceRegistry.mount({ id: b.id, source: b.source });
        }
        if (blocks.length === 0) {
          // Mount the welcome card. The mount fans out via SSE — the
          // useEffect below subscribes to that stream and will pick up
          // the resulting ui-update mount. Best-effort: if the POST
          // fails (e.g. backend unreachable) the canvas just stays
          // empty, which is the prior behavior.
          mountTemplate({ template: "lets_begin" }).catch((err) =>
            console.warn("[dynamic-surface] lets_begin auto-mount failed", err),
          );
        }
      })
      .catch((err) => console.warn("[dynamic-surface] hydration failed", err));
    return () => { cancelled = true; };
  }, [mode]);

  useEffect(() => {
    const ctrl = new AbortController();
    subscribeToDynamicStream((event) => {
      if (event.type === "ui-update") {
        const blockId = event.block.id;
        if (event.action === "unmount") {
          if (isSystemBlockId(blockId)) {
            // Persona is asking the canvas to hide a built-in block (e.g.
            // the command bar). System blocks aren't in sourceRegistry —
            // their visibility lives in systemBlocks.
            systemBlocks.hide(blockId);
          } else {
            sourceRegistry.unmount(blockId);
          }
        } else {
          if (isSystemBlockId(blockId)) {
            // Re-mount of a system block ignores `source` — it just
            // flips the visibility flag back on.
            systemBlocks.show(blockId);
          } else {
            sourceRegistry.mount({ id: blockId, source: event.block.source });
            // Auto-raise the freshly-mounted block above existing ones,
            // so e.g. a teacher's interactive_graph drawn while a PDF is
            // open lands on top instead of vanishing behind the PDF.
            // Defer one frame so React has committed the new DOM node.
            if (typeof requestAnimationFrame !== "undefined") {
              requestAnimationFrame(() => dynamicBlockRegistry.raise(blockId));
            } else {
              dynamicBlockRegistry.raise(blockId);
            }
          }
        }
      } else if (event.type === "block-data") {
        bus.publish(event.topic, event.value);
      } else if (event.type === "block-action") {
        // Defer one frame so a block mounted in the same SSE batch has had
        // time to render before we try to scroll/highlight/focus it.
        const apply = () => {
          const ok = dynamicBlockRegistry.apply(event.action, event.block_id, event.options);
          if (!ok) {
            console.warn(
              `[dynamic-surface] block-action ${event.action} on ${event.block_id} found no element`,
            );
          }
        };
        if (typeof requestAnimationFrame !== "undefined") {
          requestAnimationFrame(apply);
        } else {
          apply();
        }
      }
      // block-error / open / unknown: ignore on the surface; the bus and
      // the block's own fallback already handle the error display path.
    }, ctrl.signal).catch((err) => {
      if (err?.name !== "AbortError") {
        console.warn("[dynamic-surface] stream ended", err);
      }
    });
    return () => ctrl.abort();
  }, []);

  // Surface a live drag snapshot so we can paint grid lines + a snap target
  // overlay only while the user is mid-drag.
  const subscribeDrag = useCallback((l: () => void) => dragController.subscribe(l), []);
  const getDrag = useCallback((): DragSnapshot => dragController.get(), []);
  const drag = useSyncExternalStore(subscribeDrag, getDrag, () => null);

  // Subscribe once for any system-block visibility flip. `useSyncExternalStore`
  // returns a stable token that flips per change so React re-renders us; we
  // then re-evaluate `systemBlocks.isHidden(id)` per spec below.
  const subscribeSys = useCallback((l: () => void) => systemBlocks.subscribe(l), []);
  const getSysVersion = useCallback(
    () => SYSTEM_BLOCKS.map((b) => (systemBlocks.isHidden(b.id) ? "0" : "1")).join(""),
    [],
  );
  useSyncExternalStore(subscribeSys, getSysVersion, getSysVersion);

  if (mode === "overlay" && entries.length === 0 && !drag) return null;

  const { cols, rows } = GRID_SIZES[device];
  const gridTemplateColumns = `repeat(${cols}, 1fr)`;
  const gridTemplateRows = `repeat(${rows}, 1fr)`;

  const surfaceStyle: React.CSSProperties = mode === "fullscreen"
    ? {
        position: "absolute",
        inset: 0,
        display: "grid",
        gridTemplateColumns,
        gridTemplateRows,
        background: "var(--bw-void)",
      }
    : {
        position: "fixed",
        inset: 0,
        display: "grid",
        gridTemplateColumns,
        gridTemplateRows,
        pointerEvents: "none",
        zIndex: 25,
      };

  const snapTarget = drag ? scaleGridForDevice(drag.target, device) : null;

  return (
    <div
      data-dynamic-surface=""
      data-mode={mode}
      data-device={device}
      data-dragging={drag ? "" : undefined}
      style={surfaceStyle}
    >
      {entries.map((e) => (
        <div
          key={e.id}
          style={{ display: "contents", pointerEvents: mode === "overlay" ? "auto" : undefined }}
        >
          <Block id={e.id} source={e.source} />
        </div>
      ))}

      {/* Built-in blocks — share the grid + drag with source-eval blocks. */}
      {mode === "fullscreen" && SYSTEM_BLOCKS.map((b) => {
        if (systemBlocks.isHidden(b.id)) return null;
        const Comp = b.Component;
        return (
          <BlockShell key={b.id} id={b.id} defaultGrid={b.defaultGrid} z={50}>
            <Comp />
          </BlockShell>
        );
      })}

      {/* Drag-time grid overlay: faint lines spanning the surface so the user
          sees the cell structure they're snapping to. Pure-CSS background
          gradients keep this cheap. */}
      {drag && (
        <div
          aria-hidden
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            zIndex: 998,
            backgroundImage: `
              linear-gradient(to right, rgba(255, 255, 255, 0.10) 1px, transparent 1px),
              linear-gradient(to bottom, rgba(255, 255, 255, 0.10) 1px, transparent 1px)
            `,
            backgroundSize: `calc(100% / ${cols}) 100%, 100% calc(100% / ${rows})`,
            backgroundPosition: "0 0, 0 0",
            backgroundRepeat: "repeat",
            transition: "opacity 120ms ease",
          }}
        />
      )}

      {/* Snap target: dashed cyan rectangle on the cell the block will land
          on. Sits inside the grid so it tracks the same fr-units as blocks. */}
      {drag && snapTarget && (
        <div
          aria-hidden
          style={{
            gridColumn: `${snapTarget.x + 1} / span ${snapTarget.w}`,
            gridRow: `${snapTarget.y + 1} / span ${snapTarget.h}`,
            margin: 2,
            pointerEvents: "none",
            zIndex: 998,
            border: "2px dashed rgba(34, 211, 238, 0.85)",
            background: "rgba(34, 211, 238, 0.10)",
            borderRadius: 6,
            boxShadow: "0 0 24px rgba(34, 211, 238, 0.25)",
          }}
        />
      )}
    </div>
  );
}
