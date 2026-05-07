"use client";

import { useEffect, useSyncExternalStore } from "react";
import { Block } from "./Block";
import { bus } from "@/lib/bus";
import { sourceRegistry, type SourceEntry } from "@/lib/dynamic";
import { useDeviceClass } from "@/lib/device";
import { GRID_SIZES } from "@/lib/gridConfig";
import { fetchCanvas, mountTemplate, subscribeToDynamicStream } from "@/lib/api";
import { loadPdfjs } from "@/lib/pdfjs-loader";
import { dynamicBlockRegistry } from "@/lib/dynamicBlockRegistry";

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
        if (event.action === "unmount") {
          sourceRegistry.unmount(event.block.id);
        } else {
          sourceRegistry.mount({ id: event.block.id, source: event.block.source });
          // Auto-raise the freshly-mounted block above existing ones,
          // so e.g. a teacher's interactive_graph drawn while a PDF is
          // open lands on top instead of vanishing behind the PDF.
          // Defer one frame so React has committed the new DOM node.
          const newId = event.block.id;
          if (typeof requestAnimationFrame !== "undefined") {
            requestAnimationFrame(() => dynamicBlockRegistry.raise(newId));
          } else {
            dynamicBlockRegistry.raise(newId);
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

  if (mode === "overlay" && entries.length === 0) return null;

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

  return (
    <div data-dynamic-surface="" data-mode={mode} data-device={device} style={surfaceStyle}>
      {entries.map((e) => (
        <div
          key={e.id}
          style={{ display: "contents", pointerEvents: mode === "overlay" ? "auto" : undefined }}
        >
          <Block id={e.id} source={e.source} />
        </div>
      ))}
    </div>
  );
}
