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
      });
      window.mermaid = mermaid;
      return mermaid;
    })();
  }, []);

  return null;
}
