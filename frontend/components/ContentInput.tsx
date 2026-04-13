"use client";

import { useState, useRef } from "react";

export interface ContentResult {
  type: "text" | "pdf";
  text: string;
  file?: File;
}

export default function ContentInput({
  onSubmit,
}: {
  onSubmit: (result: ContentResult) => void;
}) {
  const [text, setText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (text.trim()) onSubmit({ type: "text", text: text.trim() });
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are supported");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError("PDF too large (max 50 MB)");
      return;
    }
    setError(null);
    setUploading(true);
    try {
      // Import dynamically to avoid circular deps
      const { uploadPdf } = await import("@/lib/api");
      const result = await uploadPdf(file);
      onSubmit({ type: "pdf", text: result.text, file });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      // Reset the file input so the same file can be re-selected
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center h-screen p-8">
      <form onSubmit={handleSubmit} className="w-full max-w-2xl space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight">beWithMe</h1>
          <p className="mt-2 text-gray-500 dark:text-gray-400">
            Paste your reading material or upload a PDF
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
        {error && (
          <p className="text-sm text-red-500 text-center">{error}</p>
        )}
        <div className="flex items-center justify-center gap-4">
          <button
            type="submit"
            disabled={!text.trim() || uploading}
            className="rounded-full bg-blue-600 px-8 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 transition-colors"
          >
            Start Reading
          </button>
          <span className="text-sm text-gray-400">or</span>
          <label
            className={`rounded-full border border-gray-300 dark:border-gray-600 px-8 py-3 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors ${
              uploading ? "opacity-40 pointer-events-none" : ""
            }`}
          >
            {uploading ? "Uploading..." : "Upload PDF"}
            <input
              ref={fileRef}
              type="file"
              accept=".pdf"
              onChange={handleFileChange}
              className="hidden"
            />
          </label>
        </div>
      </form>
    </div>
  );
}
