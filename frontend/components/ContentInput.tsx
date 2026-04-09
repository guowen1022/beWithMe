"use client";

import { useState } from "react";

export default function ContentInput({
  onSubmit,
}: {
  onSubmit: (content: string) => void;
}) {
  const [text, setText] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (text.trim()) onSubmit(text.trim());
  }

  return (
    <div className="flex flex-1 items-center justify-center h-screen p-8">
      <form onSubmit={handleSubmit} className="w-full max-w-2xl space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight">beWithMe</h1>
          <p className="mt-2 text-gray-500 dark:text-gray-400">
            Paste your reading material to get started
          </p>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={12}
          autoFocus
          className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 text-base leading-relaxed focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          placeholder="Paste a paper, article, or any text you want to read and understand..."
        />
        <div className="flex justify-center">
          <button
            type="submit"
            disabled={!text.trim()}
            className="rounded-full bg-blue-600 px-8 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 transition-colors"
          >
            Start Reading
          </button>
        </div>
      </form>
    </div>
  );
}
