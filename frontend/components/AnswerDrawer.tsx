"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import type { AgentStatus, QuestionNode } from "./Reader";
import { parseMarkdownBlocks } from "@/lib/markdownBlocks";
import LogicBlock, { type Direction } from "./LogicBlock";

// Typewriter reveal rate. BASE_RATE sets the steady feel; if the source
// text is far ahead (e.g. the final answer event just replaced the buffer
// with more content), we accelerate so reveal drains within CATCH_UP_SEC
// and the user isn't waiting long after the server stream finishes.
const BASE_RATE = 80; // chars/sec
const CATCH_UP_SEC = 0.8;
// After finishing a sentence, pause briefly before starting the next one
// so the reveal feels like natural reading cadence instead of a single
// continuous crawl.
const SENTENCE_PAUSE_MS = 500;

/**
 * Find the index (exclusive) just after the first sentence-ending
 * punctuation within `target[from, to)`. Returns -1 if no boundary found.
 */
function findSentenceEnd(target: string, from: number, to: number): number {
  for (let i = from; i < to; i++) {
    const ch = target[i];
    if (ch === "\n" && target[i + 1] === "\n") {
      return i + 1;
    }
    if (ch !== "." && ch !== "!" && ch !== "?") continue;
    const next = target[i + 1];
    if (next === undefined) return i + 1;
    if (next !== " " && next !== "\n" && next !== "\t") continue;
    let j = i + 1;
    while (j < target.length && /\s/.test(target[j])) j++;
    if (j < target.length) {
      const after = target[j];
      if (after >= "a" && after <= "z") continue;
    }
    return i + 1;
  }
  return -1;
}

function AnimatedDots() {
  const [count, setCount] = useState(1);
  useEffect(() => {
    const id = setInterval(() => setCount((c) => (c % 3) + 1), 400);
    return () => clearInterval(id);
  }, []);
  return <span>{".".repeat(count)}</span>;
}

function StatusIndicator({
  status,
  searchDetail,
}: {
  status: AgentStatus;
  searchDetail: string | null;
}) {
  if (status === "thinking") {
    return (
      <div className="flex items-center gap-3 text-sm text-gray-500">
        <span className="inline-block w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
        <span>
          Thinking
          <AnimatedDots />
        </span>
      </div>
    );
  }

  if (status === "searching") {
    return (
      <div className="space-y-1.5">
        <div className="flex items-center gap-3 text-sm text-blue-500">
          <svg
            className="w-4 h-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span>
            Searching the web
            <AnimatedDots />
          </span>
        </div>
        {searchDetail && (
          <p className="text-xs text-gray-400 ml-7 truncate">
            &ldquo;{searchDetail}&rdquo;
          </p>
        )}
      </div>
    );
  }

  return null;
}

export type BlockStates = {
  collapsed: Set<string>;
  reviewLater: Set<string>;
};

export default function AnswerDrawer({
  node,
  onClose,
  onBlockGesture,
  instant = false,
  initialBlockStates,
  onBlockStatesChange,
}: {
  node: QuestionNode;
  onClose: () => void;
  onBlockGesture: (blockText: string, direction: Direction) => void;
  instant?: boolean;
  initialBlockStates?: BlockStates;
  onBlockStatesChange?: (states: BlockStates) => void;
}) {
  const loading = node.loading;
  const status = node.status;
  const searchDetail = node.searchDetail;

  // --- Typewriter animation (unchanged) ---
  const targetRef = useRef<string>("");
  const pausedUntilRef = useRef<number>(0);
  const [displayedText, setDisplayedText] = useState<string>(
    instant ? node.displayedText : "",
  );

  useEffect(() => {
    targetRef.current = node.displayedText;
    if (instant) setDisplayedText(node.displayedText);
  }, [node.displayedText, instant]);

  useEffect(() => {
    if (instant) return;

    let rafId = 0;
    let lastTime = performance.now();

    const tick = (now: number) => {
      if (now < pausedUntilRef.current) {
        lastTime = now;
        rafId = requestAnimationFrame(tick);
        return;
      }

      const dtSec = Math.min((now - lastTime) / 1000, 0.1);
      lastTime = now;

      setDisplayedText((prev) => {
        const target = targetRef.current;
        if (prev.length >= target.length) return prev;
        const remaining = target.length - prev.length;
        const rate = Math.max(BASE_RATE, remaining / CATCH_UP_SEC);
        const advance = Math.max(1, Math.floor(rate * dtSec));
        const desiredEnd = Math.min(prev.length + advance, target.length);

        const boundary = findSentenceEnd(target, prev.length, desiredEnd);
        const stopAt = boundary === -1 ? desiredEnd : boundary;
        if (boundary !== -1 && stopAt < target.length) {
          pausedUntilRef.current = performance.now() + SENTENCE_PAUSE_MS;
        }
        return target.slice(0, stopAt);
      });

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [instant]);

  // --- Block parsing ---
  const blocks = useMemo(
    () => parseMarkdownBlocks(displayedText),
    [displayedText],
  );

  // --- Block interaction state ---
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [collapsedBlocks, setCollapsedBlocks] = useState<Set<string>>(
    () => initialBlockStates?.collapsed ?? new Set(),
  );
  const [reviewLaterBlocks, setReviewLaterBlocks] = useState<Set<string>>(
    () => initialBlockStates?.reviewLater ?? new Set(),
  );
  const [interactionModeBlock, setInteractionModeBlock] = useState<
    string | null
  >(null);

  // Sync block states back to parent for persistence across navigation
  useEffect(() => {
    onBlockStatesChange?.({ collapsed: collapsedBlocks, reviewLater: reviewLaterBlocks });
  }, [collapsedBlocks, reviewLaterBlocks, onBlockStatesChange]);

  const containerRef = useRef<HTMLDivElement>(null);

  // Keep focusedIdx in bounds as blocks grow during streaming
  useEffect(() => {
    if (focusedIdx >= blocks.length && blocks.length > 0) {
      setFocusedIdx(blocks.length - 1);
    }
  }, [blocks.length, focusedIdx]);

  const handleGesture = useCallback(
    (blockId: string, direction: Direction) => {
      const block = blocks.find((b) => b.id === blockId);
      if (!block) return;

      if (direction === "left") {
        if (interactionModeBlock === blockId) {
          // Cancel interaction mode
          setInteractionModeBlock(null);
          return;
        }
        // "Got it" — collapse + signal
        setCollapsedBlocks((prev) => new Set(prev).add(blockId));
        onBlockGesture(block.markdown, "left");
        return;
      }

      if (direction === "right" && interactionModeBlock !== blockId) {
        // Enter interaction mode
        setInteractionModeBlock(blockId);
        return;
      }

      // In interaction mode: up/down/right → fire gesture
      if (direction === "up") {
        setReviewLaterBlocks((prev) => new Set(prev).add(blockId));
        setInteractionModeBlock(null);
        onBlockGesture(block.markdown, "up");
        return;
      }
      if (direction === "down") {
        setInteractionModeBlock(null);
        onBlockGesture(block.markdown, "down");
        return;
      }
      if (direction === "right") {
        setInteractionModeBlock(null);
        onBlockGesture(block.markdown, "right");
        return;
      }
    },
    [blocks, interactionModeBlock, onBlockGesture],
  );

  const handleToggleCollapse = useCallback((blockId: string) => {
    setCollapsedBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(blockId)) next.delete(blockId);
      else next.add(blockId);
      return next;
    });
    setReviewLaterBlocks((prev) => {
      const next = new Set(prev);
      next.delete(blockId);
      return next;
    });
  }, []);

  // --- Keyboard navigation ---
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (blocks.length === 0) return;

      const focusedBlock = blocks[focusedIdx];
      if (!focusedBlock) return;

      const inIM = interactionModeBlock === focusedBlock.id;

      switch (e.key) {
        case "ArrowUp":
          e.preventDefault();
          if (inIM) {
            // "Too hard"
            handleGesture(focusedBlock.id, "up");
          } else {
            setFocusedIdx((i) => Math.max(0, i - 1));
            setInteractionModeBlock(null);
          }
          break;

        case "ArrowDown":
          e.preventDefault();
          if (inIM) {
            // "Explain more"
            handleGesture(focusedBlock.id, "down");
          } else {
            setFocusedIdx((i) => Math.min(blocks.length - 1, i + 1));
            setInteractionModeBlock(null);
          }
          break;

        case "ArrowLeft":
          e.preventDefault();
          handleGesture(focusedBlock.id, "left");
          break;

        case "ArrowRight":
          e.preventDefault();
          // If collapsed, expand first instead of entering interaction mode
          if (collapsedBlocks.has(focusedBlock.id) || reviewLaterBlocks.has(focusedBlock.id)) {
            handleToggleCollapse(focusedBlock.id);
          } else {
            handleGesture(focusedBlock.id, "right");
          }
          break;

        case "Escape":
          e.preventDefault();
          if (inIM) {
            setInteractionModeBlock(null);
          } else {
            onClose();
          }
          break;
      }
    },
    [blocks, focusedIdx, interactionModeBlock, collapsedBlocks, reviewLaterBlocks, handleGesture, handleToggleCollapse, onClose],
  );

  // Auto-focus the container so keyboard events work immediately
  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  return (
    <div
      ref={containerRef}
      data-panel="answer"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="fixed top-0 right-0 h-full w-[28rem] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-xl z-30 translate-x-0 outline-none"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-gray-100 dark:border-gray-800 px-5 py-4">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400">
            Active question
          </p>
          <h2 className="text-sm font-semibold mt-0.5 line-clamp-2 break-words">
            {node.title || node.question}
          </h2>
        </div>
        <button
          data-no-send
          onClick={onClose}
          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors shrink-0"
          aria-label="Close"
        >
          <svg
            className="w-5 h-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Keyboard hint */}
      {blocks.length > 0 && !loading && (
        <div className="px-5 py-1.5 border-b border-gray-100 dark:border-gray-800 text-[10px] text-gray-400 flex gap-3">
          <span>
            <kbd className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">
              &uarr;&darr;
            </kbd>{" "}
            navigate
          </span>
          <span>
            <kbd className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">
              &larr;
            </kbd>{" "}
            got it
          </span>
          <span>
            <kbd className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">
              &rarr;
            </kbd>{" "}
            interact
          </span>
          <span>
            <kbd className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">
              [ ]
            </kbd>{" "}
            switch panel
          </span>
        </div>
      )}

      {/* Content */}
      <div className="overflow-y-auto h-[calc(100%-4.5rem)] px-3 py-3">
        {/* Status spinner */}
        {(loading || (status !== "idle" && status !== "done")) &&
          displayedText.length === 0 && (
            <div className="px-2">
              <StatusIndicator status={status} searchDetail={searchDetail} />
            </div>
          )}

        {/* Logic blocks */}
        {blocks.length > 0 && (
          <div className="space-y-1">
            {blocks.map((block, i) => (
              <LogicBlock
                key={block.id}
                block={block}
                focused={i === focusedIdx}
                collapsed={collapsedBlocks.has(block.id)}
                reviewLater={reviewLaterBlocks.has(block.id)}
                inProgress={loading && i === blocks.length - 1}
                interactionMode={interactionModeBlock === block.id}
                onGesture={handleGesture}
                onToggleCollapse={handleToggleCollapse}
                registryId={`answer:${node.localId}:${block.id}`}
                registryKind="answer"
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
