// Idempotently load pdfjs-dist and expose it on `window.pdfjsLib` so
// dynamic blocks (which run as vanilla JS via `new Function()`) can use it
// without dealing with module resolution themselves.
//
// Worker config mirrors the existing PdfViewer.tsx pattern.

let _promise: Promise<void> | null = null;

export function loadPdfjs(): Promise<void> {
  if (_promise) return _promise;
  if (typeof window === "undefined") return Promise.resolve();
  _promise = (async () => {
    const pdfjs = await import("pdfjs-dist");
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).toString();
    (window as unknown as { pdfjsLib: typeof pdfjs }).pdfjsLib = pdfjs;
  })();
  return _promise;
}
