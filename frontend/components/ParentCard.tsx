"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { QuestionNode } from "./Reader";

/**
 * The parent of the active question, shown in the middle of the screen.
 *
 * - Selecting text inside this card creates a SIBLING of the active question
 *   (a new child of the parent), so the wrapper carries
 *   `data-selection-source="parent"` for the global selection router in
 *   Reader.tsx.
 * - Plain clicks (no text selected) pop the stack, returning the user to
 *   this question.
 */
export default function ParentCard({
  node,
  onPop,
}: {
  node: QuestionNode;
  onPop: () => void;
}) {
  function handleClick() {
    const sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) return;
    onPop();
  }

  return (
    <div
      data-selection-source="parent"
      onClick={handleClick}
      className="max-w-3xl mx-auto px-6 py-12 sm:px-12 sm:py-16 cursor-pointer group"
      title="Click to return to this question"
    >
      <div className="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm group-hover:shadow-md group-hover:border-blue-400 dark:group-hover:border-blue-500 transition-all p-8">
        {node.title && (
          <h2 className="text-xs font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400 mb-3">
            {node.title}
          </h2>
        )}
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4 italic">
          &ldquo;{node.question}&rdquo;
        </p>
        <article className="prose prose-sm dark:prose-invert max-w-none prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-li:my-0.5 selection:bg-blue-200 dark:selection:bg-blue-800 selection:text-inherit">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {node.displayedText}
          </ReactMarkdown>
        </article>
      </div>
    </div>
  );
}
