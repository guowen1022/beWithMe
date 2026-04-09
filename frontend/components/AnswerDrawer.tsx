"use client";

import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AnswerEvent } from "@/lib/api";
import type { AgentStatus } from "./Reader";

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

export default function AnswerDrawer({
  open,
  loading,
  status,
  searchDetail,
  answer,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  status: AgentStatus;
  searchDetail: string | null;
  answer: AnswerEvent | null;
  onClose: () => void;
}) {
  return (
    <div
      className={`fixed top-0 right-0 h-full w-[28rem] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-xl z-30 transition-transform duration-300 ease-in-out ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 px-5 py-4">
        <h2 className="text-sm font-semibold">Answer</h2>
        <button
          onClick={onClose}
          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
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

      {/* Content */}
      <div className="overflow-y-auto h-[calc(100%-3.5rem)] px-5 py-4">
        {(loading || (status !== "idle" && status !== "done")) && (!answer || !answer.answer) && (
          <StatusIndicator status={status} searchDetail={searchDetail} />
        )}

        {answer && answer.answer && (
          <>
            <article className="prose prose-sm dark:prose-invert max-w-none prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-li:my-0.5 prose-table:text-sm prose-pre:bg-gray-100 dark:prose-pre:bg-gray-800 prose-code:text-pink-600 dark:prose-code:text-pink-400">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {answer.answer}
              </ReactMarkdown>
            </article>
            {answer.related_interaction_ids.length > 0 && (
              <p className="mt-4 text-xs text-gray-400 border-t border-gray-100 dark:border-gray-800 pt-3">
                Drew on {answer.related_interaction_ids.length} past interaction
                {answer.related_interaction_ids.length !== 1 ? "s" : ""}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
