"use client";

import { useRef, useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LogicBlock as LogicBlockType } from "@/lib/markdownBlocks";
import { speakTextStream } from "@/lib/api";

/** Strip markdown formatting so the TTS reads prose, not syntax. */
function markdownToSpeakable(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

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

  // TTS playback state (streamed PCM via Web Audio).
  const audioCtxRef = useRef<AudioContext | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nextStartRef = useRef<number>(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const [ttsState, setTtsState] = useState<"idle" | "loading" | "playing">("idle");

  const stopAudio = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    for (const src of activeSourcesRef.current) {
      try {
        src.onended = null;
        src.stop();
      } catch {
        // already stopped
      }
    }
    activeSourcesRef.current = [];
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    nextStartRef.current = 0;
    setTtsState("idle");
  }, []);

  useEffect(() => {
    return () => stopAudio();
  }, [stopAudio]);

  const handleSpeak = useCallback(async () => {
    if (ttsState === "playing") {
      stopAudio();
      return;
    }
    if (ttsState === "loading") return;
    const text = markdownToSpeakable(block.markdown);
    if (!text) return;
    setTtsState("loading");

    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const { sampleRate, reader } = await speakTextStream(text, {
        signal: abort.signal,
      });
      if (abort.signal.aborted) return;

      const AudioCtor: typeof AudioContext =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const ctx = new AudioCtor({ sampleRate });
      audioCtxRef.current = ctx;
      nextStartRef.current = ctx.currentTime;
      activeSourcesRef.current = [];

      let leftover = new Uint8Array(0);
      let firstChunk = true;
      let readingDone = false;
      let totalScheduled = 0;
      let endedCount = 0;
      const maybeFinish = () => {
        if (readingDone && endedCount >= totalScheduled && !abort.signal.aborted) {
          stopAudio();
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (abort.signal.aborted) return;

        // Re-align on 16-bit sample boundaries across network chunks.
        const merged = new Uint8Array(leftover.byteLength + value.byteLength);
        merged.set(leftover, 0);
        merged.set(value, leftover.byteLength);
        const usable = merged.byteLength - (merged.byteLength % 2);
        if (usable < merged.byteLength) {
          leftover = merged.slice(usable);
        } else {
          leftover = new Uint8Array(0);
        }
        if (usable === 0) continue;

        const int16 = new Int16Array(merged.buffer, merged.byteOffset, usable / 2);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
          float32[i] = int16[i] / 32768;
        }

        const buf = ctx.createBuffer(1, float32.length, sampleRate);
        buf.copyToChannel(float32, 0);

        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime, nextStartRef.current);
        src.start(startAt);
        nextStartRef.current = startAt + buf.duration;
        activeSourcesRef.current.push(src);
        totalScheduled++;
        src.onended = () => {
          endedCount++;
          maybeFinish();
        };

        if (firstChunk) {
          setTtsState("playing");
          firstChunk = false;
        }
      }
      readingDone = true;
      maybeFinish();
    } catch (err) {
      if (!abort.signal.aborted) {
        console.error("TTS stream failed", err);
      }
      stopAudio();
    }
  }, [block.markdown, stopAudio, ttsState]);

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
      <button
        type="button"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          handleSpeak();
        }}
        title={ttsState === "playing" ? "Stop" : "Read aloud"}
        aria-label={ttsState === "playing" ? "Stop reading" : "Read block aloud"}
        className={`absolute top-2 right-2 z-10 rounded-full p-1.5 transition-colors shrink-0 ${
          ttsState === "playing"
            ? "bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400"
            : "text-gray-300 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
        }`}
      >
        {ttsState === "loading" ? (
          <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : ttsState === "playing" ? (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="1" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M11 5L6 9H2v6h4l5 4V5z" />
            <path d="M15.54 8.46a5 5 0 010 7.07" />
            <path d="M19.07 4.93a10 10 0 010 14.14" />
          </svg>
        )}
      </button>
      <article className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-2 prose-headings:mb-1 prose-code:text-pink-600 dark:prose-code:text-pink-400 pr-8">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {block.markdown}
        </ReactMarkdown>
      </article>
    </div>
  );
}
