"use client";

import { useState } from "react";

/**
 * Compact card pinned to the top-left of the screen when the user has
 * drilled at least one level deep, so the source passage stays visible
 * (and selectable for new top-level questions) even after the middle
 * surface has been taken over by a parent question card.
 */
export default function PinnedPassageCard({
  content,
  offsetLeft,
}: {
  content: string;
  offsetLeft: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      data-selection-source="passage"
      className={`fixed top-4 z-30 transition-all duration-300 ${
        offsetLeft ? "left-[21rem]" : "left-4"
      } ${expanded ? "w-[28rem]" : "w-72"}`}
    >
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm shadow-lg overflow-hidden">
        <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Source passage
          </span>
          <button
            data-no-send
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs px-1.5 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "−" : "+"}
          </button>
        </div>
        <div
          className={`px-3 py-2 text-xs leading-5 text-gray-700 dark:text-gray-300 selection:bg-blue-200 dark:selection:bg-blue-800 selection:text-inherit overflow-y-auto ${
            expanded ? "max-h-[60vh]" : "max-h-32"
          }`}
        >
          {content}
        </div>
      </div>
    </div>
  );
}
