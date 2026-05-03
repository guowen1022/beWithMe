"use client";

import { useEffect, useSyncExternalStore } from "react";
import { Block } from "./Block";
import { bus } from "@/lib/bus";
import { sourceRegistry, type SourceEntry } from "@/lib/dynamic";
import { useDeviceClass } from "@/lib/device";
import { fetchCanvas, subscribeToDynamicStream } from "@/lib/api";
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

  // Hydrate the registry from the user's per-user-git workspace on mount,
  // so reloads don't lose their canvas. Overlay mode skips this (the
  // reader doesn't auto-show every saved block in the corner of the page).
  useEffect(() => {
    if (mode !== "fullscreen") return;
    let cancelled = false;
    fetchCanvas()
      .then((blocks) => {
        if (cancelled) return;
        for (const b of blocks) {
          sourceRegistry.mount({ id: b.id, source: b.source });
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

  const surfaceStyle: React.CSSProperties = mode === "fullscreen"
    ? {
        position: "absolute",
        inset: 0,
        display: "grid",
        gridTemplateColumns: "repeat(160, 1fr)",
        gridTemplateRows: "repeat(90, 1fr)",
        background: "#0a0a0a",
      }
    : {
        position: "fixed",
        inset: 0,
        display: "grid",
        gridTemplateColumns: "repeat(160, 1fr)",
        gridTemplateRows: "repeat(90, 1fr)",
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
