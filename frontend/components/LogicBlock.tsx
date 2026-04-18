"use client";

import { useRef, useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LogicBlock as LogicBlockType } from "@/lib/markdownBlocks";

export type Direction = "left" | "right" | "up" | "down";

type DragPhase = "idle" | "tracking" | "radial";

// Minimum drag distance (px) before committing to a direction
const DRAG_THRESHOLD = 30;

// Direction labels and colors for the radial sub-menu
const RADIAL_OPTIONS: Record<
  Direction,
  { label: string; color: string; hoverBg: string }
> = {
  up: {
    label: "Too hard",
    color: "text-amber-600 dark:text-amber-400",
    hoverBg: "bg-amber-50 dark:bg-amber-900/30",
  },
  down: {
    label: "Explain more",
    color: "text-blue-600 dark:text-blue-400",
    hoverBg: "bg-blue-50 dark:bg-blue-900/30",
  },
  right: {
    label: "Ask a question",
    color: "text-purple-600 dark:text-purple-400",
    hoverBg: "bg-purple-50 dark:bg-purple-900/30",
  },
  left: {
    label: "Back",
    color: "text-gray-500 dark:text-gray-400",
    hoverBg: "bg-gray-50 dark:bg-gray-800",
  },
};

function computeDirection(dx: number, dy: number): Direction {
  if (Math.abs(dx) > Math.abs(dy)) {
    return dx < 0 ? "left" : "right";
  }
  return dy < 0 ? "up" : "down";
}

/**
 * Arrow icon pointing in a direction.
 */
function ArrowIcon({
  dir,
  className = "",
}: {
  dir: Direction;
  className?: string;
}) {
  const rotation = { up: -90, down: 90, left: 180, right: 0 }[dir];
  return (
    <svg
      className={`w-4 h-4 ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      style={{ transform: `rotate(${rotation}deg)` }}
    >
      <path d="M5 12h14M12 5l7 7-7 7" />
    </svg>
  );
}

export default function LogicBlock({
  block,
  focused,
  collapsed,
  reviewLater,
  inProgress,
  interactionMode,
  onGesture,
  onToggleCollapse,
}: {
  block: LogicBlockType;
  focused: boolean;
  collapsed: boolean;
  reviewLater: boolean;
  inProgress: boolean;
  interactionMode: boolean;
  onGesture: (blockId: string, direction: Direction) => void;
  onToggleCollapse: (blockId: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dragPhase = useRef<DragPhase>("idle");
  const startPos = useRef({ x: 0, y: 0 });
  const blockCenter = useRef({ x: 0, y: 0 });
  const [dragDir, setDragDir] = useState<Direction | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [radialHover, setRadialHover] = useState<Direction | null>(null);
  const radialHoverRef = useRef<Direction | null>(null);

  // Scroll into view when focused
  useEffect(() => {
    if (focused && ref.current) {
      ref.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [focused]);

  const cleanup = useCallback(() => {
    dragPhase.current = "idle";
    setDragDir(null);
    setDragOffset({ x: 0, y: 0 });
    setRadialHover(null);
    radialHoverRef.current = null;
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (inProgress || collapsed || reviewLater) return;
      e.preventDefault();
      // Re-focus the drawer container so keyboard keeps working
      const drawer = (e.currentTarget as HTMLElement).closest("[data-panel='answer']") as HTMLElement | null;
      drawer?.focus();
      dragPhase.current = "tracking";
      startPos.current = { x: e.clientX, y: e.clientY };
      if (ref.current) {
        const rect = ref.current.getBoundingClientRect();
        blockCenter.current = {
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        };
      }
    },
    [inProgress, collapsed, reviewLater],
  );

  useEffect(() => {
    function handleMouseMove(e: MouseEvent) {
      if (dragPhase.current === "idle") return;

      const dx = e.clientX - startPos.current.x;
      const dy = e.clientY - startPos.current.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dragPhase.current === "tracking") {
        setDragOffset({ x: dx * 0.3, y: dy * 0.3 });
        if (dist > DRAG_THRESHOLD) {
          const dir = computeDirection(dx, dy);
          if (dir === "left") {
            // Immediate "got it"
            onGesture(block.id, "left");
            cleanup();
            return;
          }
          if (dir === "right") {
            // Transition to radial sub-menu
            dragPhase.current = "radial";
            setDragOffset({ x: 0, y: 0 });
            setDragDir(null);
          }
          // up/down during phase 1 are ignored — only left/right matter
        } else {
          setDragDir(dist > 10 ? computeDirection(dx, dy) : null);
        }
      }

      if (dragPhase.current === "radial") {
        // Compute direction from block center
        const rx = e.clientX - blockCenter.current.x;
        const ry = e.clientY - blockCenter.current.y;
        if (Math.sqrt(rx * rx + ry * ry) > 15) {
          const dir = computeDirection(rx, ry);
          setRadialHover(dir);
          radialHoverRef.current = dir;
        } else {
          setRadialHover(null);
          radialHoverRef.current = null;
        }
      }
    }

    function handleMouseUp() {
      if (dragPhase.current === "radial" && radialHoverRef.current) {
        onGesture(block.id, radialHoverRef.current);
      }
      cleanup();
    }

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [block.id, onGesture, cleanup]);

  // --- Collapsed states ---
  if (collapsed) {
    return (
      <div
        ref={ref}
        onClick={() => onToggleCollapse(block.id)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer transition-colors text-sm ${
          focused ? "ring-2 ring-blue-400" : ""
        } bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/30`}
      >
        <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M9 5l7 7-7 7" />
        </svg>
        <span>{block.summary}</span>
      </div>
    );
  }

  if (reviewLater) {
    return (
      <div
        ref={ref}
        onClick={() => onToggleCollapse(block.id)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer transition-colors text-sm ${
          focused ? "ring-2 ring-blue-400" : ""
        } bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30`}
      >
        <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>{block.summary}</span>
      </div>
    );
  }

  // --- Radial sub-menu overlay ---
  const radialOverlay = (interactionMode || dragPhase.current === "radial") && (
    <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-white/80 dark:bg-gray-900/80 backdrop-blur-sm">
      <div className="relative w-full h-full min-h-[120px] flex items-center justify-center">
        {/* Up */}
        <div
          className={`absolute top-1 left-1/2 -translate-x-1/2 flex flex-col items-center gap-0.5 px-2 py-1 rounded transition-colors ${
            radialHover === "up" ? RADIAL_OPTIONS.up.hoverBg : ""
          }`}
        >
          <ArrowIcon dir="up" className={RADIAL_OPTIONS.up.color} />
          <span className={`text-[10px] font-medium ${RADIAL_OPTIONS.up.color}`}>
            {RADIAL_OPTIONS.up.label}
          </span>
        </div>
        {/* Down */}
        <div
          className={`absolute bottom-1 left-1/2 -translate-x-1/2 flex flex-col items-center gap-0.5 px-2 py-1 rounded transition-colors ${
            radialHover === "down" ? RADIAL_OPTIONS.down.hoverBg : ""
          }`}
        >
          <span className={`text-[10px] font-medium ${RADIAL_OPTIONS.down.color}`}>
            {RADIAL_OPTIONS.down.label}
          </span>
          <ArrowIcon dir="down" className={RADIAL_OPTIONS.down.color} />
        </div>
        {/* Left (back) */}
        <div
          className={`absolute left-1 top-1/2 -translate-y-1/2 flex items-center gap-1 px-2 py-1 rounded transition-colors ${
            radialHover === "left" ? RADIAL_OPTIONS.left.hoverBg : ""
          }`}
        >
          <ArrowIcon dir="left" className={RADIAL_OPTIONS.left.color} />
          <span className={`text-[10px] font-medium ${RADIAL_OPTIONS.left.color}`}>
            {RADIAL_OPTIONS.left.label}
          </span>
        </div>
        {/* Right (ask) */}
        <div
          className={`absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1 px-2 py-1 rounded transition-colors ${
            radialHover === "right" ? RADIAL_OPTIONS.right.hoverBg : ""
          }`}
        >
          <span className={`text-[10px] font-medium ${RADIAL_OPTIONS.right.color}`}>
            {RADIAL_OPTIONS.right.label}
          </span>
          <ArrowIcon dir="right" className={RADIAL_OPTIONS.right.color} />
        </div>
      </div>
    </div>
  );

  // --- Drag hint color ---
  let dragBg = "";
  if (dragPhase.current === "tracking" && dragDir) {
    dragBg =
      dragDir === "left"
        ? "bg-green-50/60 dark:bg-green-900/20"
        : dragDir === "right"
          ? "bg-blue-50/60 dark:bg-blue-900/20"
          : "";
  }

  return (
    <div
      ref={ref}
      onMouseDown={handleMouseDown}
      className={`relative rounded-lg px-3 py-2 transition-all select-none cursor-default ${
        focused
          ? "ring-2 ring-blue-400 bg-blue-50/40 dark:bg-blue-900/20"
          : "hover:bg-gray-50 dark:hover:bg-gray-800/50"
      } ${dragBg} ${inProgress ? "opacity-70" : ""}`}
      style={
        dragPhase.current === "tracking"
          ? {
              transform: `translate(${dragOffset.x}px, ${dragOffset.y}px)`,
              transition: "none",
            }
          : undefined
      }
    >
      {radialOverlay}
      <article className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-2 prose-headings:mb-1 prose-code:text-pink-600 dark:prose-code:text-pink-400">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {block.markdown}
        </ReactMarkdown>
      </article>
    </div>
  );
}
