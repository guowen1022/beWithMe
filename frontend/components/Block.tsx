"use client";

import { useEffect, useMemo, useRef } from "react";
import { bus } from "@/lib/bus";
import { evalBlockSource, normalizeStyle, reportBlockError } from "@/lib/dynamic";

type Props = { id: string; source: string };

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
    try {
      block.run(el, bus, (cb) => cleanups.push(cb));
    } catch (err) {
      const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      console.error(`[block ${id}] run error`, err);
      reportBlockError(id, msg);
    }
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
