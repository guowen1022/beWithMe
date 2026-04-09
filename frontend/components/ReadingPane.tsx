"use client";

import { useEffect, useCallback } from "react";

export default function ReadingPane({
  content,
  onSelection,
}: {
  content: string;
  onSelection: (text: string) => void;
}) {
  const handleMouseUp = useCallback(() => {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (text && text.length > 0) {
      onSelection(text);
    }
  }, [onSelection]);

  useEffect(() => {
    document.addEventListener("mouseup", handleMouseUp);
    return () => document.removeEventListener("mouseup", handleMouseUp);
  }, [handleMouseUp]);

  // Split content into paragraphs for nice rendering
  const paragraphs = content.split(/\n\s*\n/).filter(Boolean);

  return (
    <article className="max-w-3xl mx-auto px-6 py-12 sm:px-12 sm:py-16">
      {paragraphs.map((para, i) => (
        <p
          key={i}
          className="mb-6 text-lg leading-8 text-gray-800 dark:text-gray-200 selection:bg-blue-200 dark:selection:bg-blue-800 selection:text-inherit"
        >
          {para}
        </p>
      ))}
    </article>
  );
}
