"use client";

import { useState, useRef } from "react";

export interface ContentResult {
  type: "text" | "pdf" | "browser";
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
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [handoffActive, setHandoffActive] = useState(false);
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
      const { uploadPdf } = await import("@/lib/api");
      const result = await uploadPdf(file);
      onSubmit({ type: "pdf", text: result.text, file });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleUrlFetch() {
    const url = urlValue.trim();
    if (!url) return;
    setError(null);
    setHandoffActive(false);
    setUploading(true);
    try {
      const { uploadUrl, getBrowserStatus } = await import("@/lib/api");
      const result = await uploadUrl(url);
      const browser = await getBrowserStatus().catch(() => ({ headed: false }));
      onSubmit({ type: browser.headed ? "browser" : "text", text: result.text });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to fetch URL";
      setError(msg);
      if (msg.includes("anti-bot") || msg.includes("captcha")) {
        setHandoffActive(true);
      }
    } finally {
      setUploading(false);
    }
  }

  async function handleHandoff() {
    const url = urlValue.trim();
    if (!url) return;
    setError(null);
    setUploading(true);
    try {
      const { browserHandoff } = await import("@/lib/api");
      const result = await browserHandoff(url);
      setError(result.message);
      setHandoffActive(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open browser");
      setHandoffActive(false);
    } finally {
      setUploading(false);
    }
  }

  async function handleResume() {
    setError(null);
    setUploading(true);
    try {
      const { browserResume } = await import("@/lib/api");
      const result = await browserResume();
      setHandoffActive(false);
      onSubmit({ type: "browser", text: result.text });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to extract content");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center h-screen p-8">
      <form onSubmit={handleSubmit} className="w-full max-w-2xl space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight">beWithMe</h1>
          <p className="mt-2 text-gray-500 dark:text-gray-400">
            Paste your reading material, upload a PDF, or open a URL
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
        {showUrlInput && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                type="url"
                value={urlValue}
                onChange={(e) => setUrlValue(e.target.value)}
                autoFocus
                disabled={uploading}
                placeholder="https://example.com/article"
                className="flex-1 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleUrlFetch();
                  }
                }}
              />
              <button
                type="button"
                onClick={handleUrlFetch}
                disabled={!urlValue.trim() || uploading}
                className="rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 transition-colors"
              >
                {uploading ? "Rendering page..." : "Fetch"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowUrlInput(false);
                  setUrlValue("");
                  setError(null);
                  setHandoffActive(false);
                }}
                disabled={uploading}
                className="rounded-full border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            {handoffActive && (
              <div className="flex items-center justify-center gap-3">
                <button
                  type="button"
                  onClick={handleHandoff}
                  disabled={uploading}
                  className="rounded-full bg-amber-600 px-5 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-40 transition-colors"
                >
                  Open in Browser
                </button>
                <button
                  type="button"
                  onClick={handleResume}
                  disabled={uploading}
                  className="rounded-full bg-green-600 px-5 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-40 transition-colors"
                >
                  Resume &amp; Read
                </button>
              </div>
            )}
          </div>
        )}
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
          <span className="text-sm text-gray-400">or</span>
          <button
            type="button"
            onClick={() => {
              setShowUrlInput((v) => !v);
              setError(null);
              setHandoffActive(false);
            }}
            disabled={uploading}
            className="rounded-full border border-gray-300 dark:border-gray-600 px-8 py-3 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors disabled:opacity-40"
          >
            Open URL
          </button>
        </div>
      </form>
    </div>
  );
}
