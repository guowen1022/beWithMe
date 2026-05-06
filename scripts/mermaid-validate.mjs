#!/usr/bin/env node
// Validate mermaid source by running mermaid.parse() in Node.
// Reads source from stdin. Exits 0 on success ("OK\n" on stdout). On
// syntax error, writes the error message to stderr and exits 1.
//
// Why: the teacher's LLM sometimes emits invalid mermaid (e.g. unquoted
// parens inside `[...]` labels). Without validation, the broken source
// is mounted on the user's canvas and they see a parse error tooltip.
// With validation, the tool returns the error to the LLM as a tool
// result and the LLM retries in the same turn.
//
// Quirk: mermaid v11 calls DOMPurify.addHook during/after parse, which
// fails in Node because DOMPurify needs a DOM. The error fires AFTER
// the actual parse completes, so we treat "addHook is not a function"
// as a success signal.

// Mermaid lives in frontend/node_modules — ESM doesn't fall back through
// parent dirs the way CJS does, so import via a path relative to this
// file rather than the bare specifier.
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
const mermaidEntry = resolve(__dirname, "../frontend/node_modules/mermaid/dist/mermaid.core.mjs");
const mermaid = (await import(mermaidEntry)).default;

let src = "";
process.stdin.setEncoding("utf-8");
process.stdin.on("data", (chunk) => (src += chunk));
process.stdin.on("end", async () => {
  if (!src.trim()) {
    process.stderr.write("empty input\n");
    process.exit(2);
  }
  try {
    await mermaid.parse(src);
    process.stdout.write("OK\n");
    process.exit(0);
  } catch (err) {
    const msg = (err && err.message) ? err.message : String(err);
    if (/addHook is not a function/i.test(msg)) {
      // Parse succeeded; DOMPurify's post-parse side effect failed
      // because we're in Node. Not a syntax problem.
      process.stdout.write("OK\n");
      process.exit(0);
    }
    process.stderr.write(msg.split("\n")[0] + "\n");
    process.exit(1);
  }
});
