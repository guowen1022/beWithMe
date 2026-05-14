"use client";

import { useEffect } from "react";

// Silero-VAD's wasm + onnx assets total ~36 MB and dominate the cold-start
// mic init (~3–5s on macOS even with fast disk). createMicVad() only runs
// when the user first hits the mic, so the user eats that wait. By
// kicking off plain HTTP fetches at app startup the bytes land in the
// browser cache; MicVAD.new() then reads them straight from disk.
//
// We intentionally don't call MicVAD.new() here — that would also call
// getUserMedia() and trigger the OS mic-permission prompt before the
// user has done anything to ask for it.
//
// Mirrors MermaidLoader's window.__xxxReady idempotency pattern.

declare global {
  interface Window {
    __vadAssetsReady?: Promise<void>;
  }
}

const ASSETS = [
  "/vad/silero_vad_v5.onnx",
  "/vad/ort-wasm-simd-threaded.wasm",
  "/vad/ort-wasm-simd-threaded.mjs",
  "/vad/vad.worklet.bundle.min.js",
];

export default function VadPrewarm() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.__vadAssetsReady) return;

    window.__vadAssetsReady = (async () => {
      const t0 = performance.now();
      // Warm the JS bundle (resolves the dynamic import in createMicVad()).
      const jsP = import("@ricky0123/vad-web").catch(() => {});
      // Warm the wasm + onnx + worklet assets via plain fetch into HTTP cache.
      const assetP = Promise.all(
        ASSETS.map((url) =>
          fetch(url, { cache: "force-cache" }).catch(() => {})
        )
      );
      await Promise.all([jsP, assetP]);
      console.log(
        `[vad.prewarm] assets cached in ${Math.round(performance.now() - t0)}ms`
      );
    })();
  }, []);

  return null;
}
