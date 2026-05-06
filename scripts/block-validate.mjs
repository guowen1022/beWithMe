#!/usr/bin/env node
// Validate a dynamic block source before SSE-mounting it on the user's
// canvas. Reads the JS source from stdin (the parens-wrapped object
// expression that mount_template / request_ui_block / engineer LLM
// produces). Exits 0 + "OK" on success; exits 1 + error message on
// stderr on failure.
//
// Two layers:
//   1. SYNTAX  — does the source parse? Catches typos, unclosed braces,
//                bad escapes, partial output from a truncated LLM stream.
//   2. SHAPE   — does it evaluate to a block-shaped object? Catches
//                missing/wrong-typed `id`, `grid`, `run`. Mirrors the
//                checks in frontend/lib/dynamic.evalBlockSource so a
//                source that passes here is guaranteed to make it past
//                the frontend's first hurdle.
//
// Layer 3 (calling run() in a mocked DOM) is out of scope — too much
// browser surface to mock reliably; runtime errors there get caught by
// the frontend's existing reportBlockError path.

import vm from "node:vm";

let src = "";
process.stdin.setEncoding("utf-8");
process.stdin.on("data", (chunk) => (src += chunk));
process.stdin.on("end", () => {
  if (!src.trim()) {
    process.stderr.write("empty source\n");
    process.exit(2);
  }

  // ─── Layer 1: syntax ─────────────────────────────────────────────────
  // Wrap exactly like the frontend's evalBlockSource (`new Function`):
  // "use strict"; return (<src>);  — so any syntax error here is the
  // same one the browser would throw on mount.
  let script;
  try {
    script = new vm.Script('"use strict"; (' + src + ");");
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    process.stderr.write("syntax: " + msg.split("\n")[0] + "\n");
    process.exit(1);
  }

  // ─── Layer 2: shape ──────────────────────────────────────────────────
  // Run the script in a vm context with permissive but minimal globals.
  // Block sources occasionally reference `document` / `window` at the
  // module level (rare but legal); stubs prevent ReferenceError during
  // the structural check. Anything heavier (mermaid, fetch, bus) only
  // runs inside `run()`, which we deliberately don't invoke.
  const sandbox = vm.createContext({
    console: { log() {}, warn() {}, error() {} },
    document: undefined,
    window: undefined,
    requestAnimationFrame: () => 0,
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
  });

  let block;
  try {
    block = script.runInContext(sandbox, { timeout: 1000 });
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    process.stderr.write("eval: " + msg.split("\n")[0] + "\n");
    process.exit(1);
  }

  if (!block || typeof block !== "object") {
    process.stderr.write("shape: source did not evaluate to an object\n");
    process.exit(1);
  }
  if (typeof block.id !== "string" || !block.id) {
    process.stderr.write("shape: block.id must be a non-empty string\n");
    process.exit(1);
  }
  if (typeof block.run !== "function") {
    process.stderr.write("shape: block.run must be a function\n");
    process.exit(1);
  }
  const g = block.grid;
  if (!g || typeof g !== "object") {
    process.stderr.write("shape: block.grid is required\n");
    process.exit(1);
  }
  for (const k of ["x", "y", "w", "h"]) {
    if (typeof g[k] !== "number" || !Number.isFinite(g[k])) {
      process.stderr.write(`shape: block.grid.${k} must be a finite number\n`);
      process.exit(1);
    }
  }
  if (g.w < 1 || g.h < 1 || g.x < 0 || g.y < 0) {
    process.stderr.write(
      `shape: block.grid out of range — x=${g.x} y=${g.y} w=${g.w} h=${g.h} (need x≥0, y≥0, w≥1, h≥1)\n`
    );
    process.exit(1);
  }

  process.stdout.write("OK\n");
  process.exit(0);
});
