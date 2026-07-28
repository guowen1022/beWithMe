"use client";

import { useEffect } from "react";

// katex's .d.ts declares `export = katex`. Under esModuleInterop a default
// import's `typeof` widens to the object intersected with the module
// namespace, which nothing actually assignable satisfies. Naming the module
// type directly avoids that intersection entirely.
type Katex = typeof import("katex");

declare global {
  interface Window {
    katex?: Katex;
    __katexReady?: Promise<Katex>;
  }
}

export default function KaTeXGlobal() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.__katexReady) return;

    window.__katexReady = (async () => {
      const mod = await import("katex");
      // With `export =`, the interop shape is `{ default: katex }`; `mod.default`
      // is the katex object itself. Unchanged from before — type-only fix.
      const katexRuntime = mod.default as Katex;
      window.katex = katexRuntime;
      return katexRuntime;
    })();
  }, []);

  return null;
}
