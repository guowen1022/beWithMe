"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// Configure pdf.js worker — must be in same module as <Document/> per react-pdf docs.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

export default function PdfViewer({
  file,
  onSelection,
}: {
  file: File;
  onSelection: (text: string) => void;
}) {
  const [numPages, setNumPages] = useState<number>(0);
  const [containerWidth, setContainerWidth] = useState<number>(700);
  const containerRef = useRef<HTMLDivElement>(null);

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
          <Page
            key={i + 1}
            pageNumber={i + 1}
            width={Math.min(containerWidth - 32, 900)}
            className="mb-4 shadow-sm"
            renderAnnotationLayer
            renderTextLayer
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
