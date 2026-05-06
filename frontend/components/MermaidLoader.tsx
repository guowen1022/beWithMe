"use client";

import { useEffect } from "react";

declare global {
  interface Window {
    mermaid?: typeof import("mermaid").default;
    __mermaidReady?: Promise<typeof import("mermaid").default>;
  }
}

export default function MermaidLoader() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.__mermaidReady) return;

    window.__mermaidReady = (async () => {
      const mod = await import("mermaid");
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "loose",
        fontFamily:
          'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        // Tighten internal diagram padding so the rendered viewBox is closer
        // to the diagram's actual content. interactive_graph then further
        // tightens via getBBox post-render. Default for most of these is 8;
        // 4 keeps a sliver of breathing room without wasting space.
        flowchart: { padding: 4, diagramPadding: 4 },
        sequence: { diagramMarginX: 8, diagramMarginY: 4 },
        gantt: { leftPadding: 24, topPadding: 24 },
      });
      window.mermaid = mermaid;
      return mermaid;
    })();
  }, []);

  return null;
}
