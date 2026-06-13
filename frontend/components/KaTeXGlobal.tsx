"use client";

import { useEffect } from "react";
import type katex from "katex";

declare global {
  interface Window {
    katex?: typeof katex;
    __katexReady?: Promise<typeof katex>;
  }
}

export default function KaTeXGlobal() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.__katexReady) return;

    window.__katexReady = (async () => {
      const mod = await import("katex");
      window.katex = mod.default;
      return mod.default;
    })();
  }, []);

  return null;
}
