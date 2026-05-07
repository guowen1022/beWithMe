"use client";

// Grid-cell wrapper for system blocks (built-in React components living on
// the same canvas as source-eval blocks). Reuses blockLayout overrides +
// startBlockDrag so the shell behaves identically to a regular Block: the
// hover-revealed grip drags it to a new cell, snap+overshoot, persisted.
// Render any React subtree as `children`.

import { useCallback, useRef, useSyncExternalStore } from "react";
import { useDeviceClass } from "@/lib/device";
import { scaleGridForDevice, type GridCoords } from "@/lib/gridConfig";
import { blockLayout } from "@/lib/blockLayout";
import { startBlockDrag } from "@/lib/blockDrag";

type Props = {
  id: string;
  defaultGrid: GridCoords;
  draggable?: boolean;
  z?: number;
  contentStyle?: React.CSSProperties;
  children: React.ReactNode;
};

export function BlockShell({
  id,
  defaultGrid,
  draggable = true,
  z = 0,
  contentStyle,
  children,
}: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const device = useDeviceClass();

  const subscribeLayout = useCallback(
    (l: () => void) => blockLayout.subscribe(l),
    [],
  );
  const getLayout = useCallback(() => blockLayout.get(id), [id]);
  const layoutOverride = useSyncExternalStore(subscribeLayout, getLayout, getLayout);

  const baseGrid = layoutOverride ?? defaultGrid;
  const scaled = scaleGridForDevice(baseGrid, device);
  const gridStyle: React.CSSProperties = {
    gridColumn: `${scaled.x + 1} / span ${scaled.w}`,
    gridRow: `${scaled.y + 1} / span ${scaled.h}`,
    overflow: "hidden",
    position: "relative",
    zIndex: z,
  };

  const onDragStart = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!draggable) return;
    e.preventDefault();
    e.stopPropagation();
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const surface = wrapper.closest<HTMLElement>("[data-dynamic-surface]");
    if (!surface) return;
    startBlockDrag({
      id,
      wrapper,
      surface,
      device,
      startClientX: e.clientX,
      startClientY: e.clientY,
      startCoords: blockLayout.get(id) ?? defaultGrid,
    });
  };

  return (
    <div
      ref={wrapperRef}
      data-block-id={id}
      data-system-block=""
      className="group"
      style={gridStyle}
    >
      <div style={{ position: "absolute", inset: 0, ...contentStyle }}>
        {children}
      </div>
      {draggable && (
        <button
          type="button"
          onPointerDown={onDragStart}
          aria-label="Drag block"
          title="Drag to move"
          className="absolute top-1.5 left-1.5 w-6 h-6 rounded
                     bg-black/45 border border-white/15 backdrop-blur-sm
                     opacity-0 group-hover:opacity-90 hover:opacity-100
                     transition-opacity cursor-grab active:cursor-grabbing
                     text-white/80 text-[13px] leading-none flex items-center justify-center
                     select-none"
          style={{ touchAction: "none", zIndex: 1000 }}
        >
          ⠿
        </button>
      )}
    </div>
  );
}
