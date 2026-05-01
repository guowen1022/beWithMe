"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { useRegisterBlock } from "@/lib/blockRegistry";

function RegisteredPdfPage({
  pageNumber,
  width,
  devicePixelRatio,
}: {
  pageNumber: number;
  width: number;
  devicePixelRatio: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useRegisterBlock({ id: `pdf:page-${pageNumber}`, kind: "pdf-page", ref });
  return (
    <div ref={ref}>
      <Page
        pageNumber={pageNumber}
        width={width}
        devicePixelRatio={devicePixelRatio}
        className="mb-4 shadow-sm"
        renderAnnotationLayer
        renderTextLayer
      />
    </div>
  );
}

// Configure pdf.js worker — must be in same module as <Document/> per react-pdf docs.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

export default function PdfViewer({
  file,
  onSelection,
  scrollToText,
}: {
  file: File;
  onSelection: (text: string) => void;
  scrollToText?: string | null;
}) {
  const [numPages, setNumPages] = useState<number>(0);
  const [containerWidth, setContainerWidth] = useState<number>(700);
  const containerRef = useRef<HTMLDivElement>(null);

  // Render canvas at device pixel ratio so text isn't blurry on retina.
  // Cap at 2 — going higher costs memory/CPU without visible gain.
  const dpr =
    typeof window === "undefined"
      ? 1
      : Math.min(window.devicePixelRatio || 1, 2);

  // Page width in CSS pixels. Cap at 760 — a typical readable column.
  // Larger pages just stretch the whitespace without making text more legible.
  const pageWidth = Math.min(containerWidth - 32, 760);

  // Track container width for responsive page sizing.
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }) {
    setNumPages(numPages);
  }

  // Text selection — react-pdf renders an invisible text layer over the
  // canvas, so window.getSelection() picks up the selected PDF text.
  const handleMouseUp = useCallback(() => {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (text && text.length > 0) {
      onSelection(text);
    }
  }, [onSelection]);

  // Create a stable object URL from the File to avoid re-loading on
  // every render. Revoke on unmount.
  const [fileUrl, setFileUrl] = useState<string>("");
  useEffect(() => {
    const url = URL.createObjectURL(file);
    setFileUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Scroll to the page containing scrollToText anchor
  useEffect(() => {
    if (!scrollToText || !containerRef.current) return;
    // Strip the timestamp suffix (after |||)
    const anchor = scrollToText.split("|||")[0];
    if (!anchor || anchor.length < 10) return;

    // Normalize: collapse whitespace for matching against PDF text layer
    const normalize = (s: string) => s.replace(/\s+/g, " ").toLowerCase().trim();
    const normAnchor = normalize(anchor);

    // Try progressively shorter substrings until we find a match
    const pages = containerRef.current.querySelectorAll(".react-pdf__Page");
    const pageTexts: { page: Element; text: string }[] = [];
    for (const page of pages) {
      const textLayer = page.querySelector(".react-pdf__Page__textContent");
      if (textLayer) {
        pageTexts.push({ page, text: normalize(textLayer.textContent || "") });
      }
    }

    // Try lengths: 200, 100, 60, 30 chars of the anchor
    const lengths = [200, 100, 60, 30];
    for (const len of lengths) {
      const snippet = normAnchor.slice(0, Math.min(len, normAnchor.length));
      if (snippet.length < 10) continue;
      for (const { page, text } of pageTexts) {
        if (text.includes(snippet)) {
          // Scroll the container directly (scrollIntoView doesn't work with nested scroll)
          const scrollParent = containerRef.current!.closest("[data-panel='center']") as HTMLElement | null;
          if (scrollParent) {
            scrollParent.scrollTo({ top: (page as HTMLElement).offsetTop - scrollParent.offsetTop, behavior: "smooth" });
          } else {
            page.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          const el = page as HTMLElement;
          el.style.outline = "3px solid rgba(250, 204, 21, 0.6)";
          el.style.outlineOffset = "4px";
          setTimeout(() => { el.style.outline = ""; el.style.outlineOffset = ""; }, 2000);
          return;
        }
      }
    }

    // Last resort: word-level matching
    const words = normAnchor.split(" ").filter(w => w.length > 3).slice(0, 6);
    if (words.length >= 2) {
      for (const { page, text } of pageTexts) {
        const hits = words.filter(w => text.includes(w));
        if (hits.length >= Math.min(4, words.length)) {
          const scrollParent = containerRef.current!.closest("[data-panel='center']") as HTMLElement | null;
          if (scrollParent) {
            scrollParent.scrollTo({ top: (page as HTMLElement).offsetTop - scrollParent.offsetTop, behavior: "smooth" });
          } else {
            page.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          const el = page as HTMLElement;
          el.style.outline = "3px solid rgba(250, 204, 21, 0.6)";
          el.style.outlineOffset = "4px";
          setTimeout(() => { el.style.outline = ""; el.style.outlineOffset = ""; }, 2000);
          return;
        }
      }
    }
  }, [scrollToText]);

  if (!fileUrl) return null;

  return (
    <div
      ref={containerRef}
      className="max-w-4xl mx-auto px-4 py-8"
      onMouseUp={handleMouseUp}
    >
      <Document
        file={fileUrl}
        onLoadSuccess={onDocumentLoadSuccess}
        loading={
          <div className="flex items-center justify-center py-20 text-gray-400">
            Loading PDF...
          </div>
        }
        error={
          <div className="flex items-center justify-center py-20 text-red-500">
            Failed to load PDF. Please try a different file.
          </div>
        }
      >
        {Array.from({ length: numPages }, (_, i) => (
          <RegisteredPdfPage
            key={i + 1}
            pageNumber={i + 1}
            width={pageWidth}
            devicePixelRatio={dpr}
          />
        ))}
      </Document>
      {numPages > 0 && (
        <p className="text-center text-xs text-gray-400 mt-4">
          {numPages} page{numPages !== 1 ? "s" : ""}
        </p>
      )}
    </div>
  );
}
