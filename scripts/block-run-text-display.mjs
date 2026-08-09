#!/usr/bin/env node
// Headless runner for text_display block sources.
//
// Reads the parens-wrapped block source from stdin, eval's it in a vm
// sandbox with a minimal DOM stub + a real `marked` instance for
// helpers.markdown, runs `block.run(root, bus, cleanup, helpers)`, and
// writes the body element's innerHTML to stdout. Mirrors what
// frontend/components/Block.tsx does at mount time, scoped to the
// surface text_display.js touches.
//
// Tests use this to assert that a given markdown payload renders to
// the expected HTML (e.g. a `|...|...|` table input → <table>) without
// driving an actual browser.
//
// Exits 0 on success with the body HTML on stdout. Exits 1 + a short
// error on stderr otherwise.

import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import vm from "node:vm";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Pull marked from the frontend's node_modules so this script doesn't
// need its own dep tree.
// pathToFileURL, not a bare path: on Windows an absolute path starts with a
// drive letter and Node's ESM loader reads "d:" as an unsupported URL scheme
// (ERR_UNSUPPORTED_ESM_URL_SCHEME). POSIX tolerates the bare path, so this
// only ever failed on Windows.
const { marked } = await import(
  pathToFileURL(
    path.join(__dirname, "..", "frontend", "node_modules", "marked", "lib", "marked.esm.js")
  ).href
);
marked.setOptions({ gfm: true, breaks: true });
// Mirror the host's renderer override (frontend/components/Block.tsx)
// so the test harness sees the same XSS escaping behavior the browser
// will: raw <html> tokens in the input become escaped text.
function _escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
marked.use({
  renderer: {
    html(token) { return _escapeHtml(token.text || ""); },
  },
});

let src = "";
process.stdin.setEncoding("utf-8");
for await (const chunk of process.stdin) src += chunk;

if (!src.trim()) {
  process.stderr.write("empty source\n");
  process.exit(2);
}

// ─── Minimal DOM stub ──────────────────────────────────────────────────
// Just enough surface for text_display.js: createElement returning a
// node with style.cssText, textContent, innerHTML, appendChild,
// addEventListener, contains. Enough for the run() body — selection
// machinery is harmless here because window.getSelection returns null.

function makeNode(tag) {
  const node = {
    nodeName: (tag || "DIV").toUpperCase(),
    tagName: (tag || "DIV").toUpperCase(),
    style: { cssText: "" },
    textContent: "",
    innerHTML: "",
    children: [],
    listeners: {},
    appendChild(child) { this.children.push(child); child.parent = this; return child; },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i >= 0) this.children.splice(i, 1);
      return child;
    },
    addEventListener(ev, fn) {
      (this.listeners[ev] = this.listeners[ev] || []).push(fn);
    },
    removeEventListener(ev, fn) {
      const arr = this.listeners[ev] || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    contains(other) {
      if (other === this) return true;
      for (const c of this.children) {
        if (c === other) return true;
        if (typeof c.contains === "function" && c.contains(other)) return true;
      }
      return false;
    },
    getAttribute() { return null; },
    setAttribute() {},
  };
  return node;
}

const documentStub = {
  createElement(tag) { return makeNode(tag); },
  addEventListener() {},
  removeEventListener() {},
};
const windowStub = {
  getSelection() { return null; },
};

const sandbox = vm.createContext({
  console: { log() {}, warn() {}, error() {} },
  document: documentStub,
  window: windowStub,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
});

let block;
try {
  const script = new vm.Script('"use strict"; (' + src + ");");
  block = script.runInContext(sandbox, { timeout: 1000 });
} catch (err) {
  process.stderr.write("eval: " + (err.message || String(err)).split("\n")[0] + "\n");
  process.exit(1);
}

if (!block || typeof block.run !== "function") {
  process.stderr.write("shape: source did not evaluate to a runnable block\n");
  process.exit(1);
}

// ─── Run the block ─────────────────────────────────────────────────────
const root = makeNode("div");
const bus = {
  subscribe(_topic, _fn) { return () => {}; },
  publish() {},
};
const cleanups = [];
const cleanup = (fn) => cleanups.push(fn);

const helpers = {
  reportState() {},
  backend: {},
  blockId: block.id,
  audio: {
    startVad: async () => ({ stop: () => {} }),
    transcribe: async () => ({ text: "", duration_seconds: 0 }),
    stopAll() {},
  },
  // The actual unit under test — the real `marked` instance, same
  // configuration the host uses.
  markdown(text) {
    return marked.parse(text || "", { async: false });
  },
};

try {
  block.run(root, bus, cleanup, helpers);
} catch (err) {
  process.stderr.write("run: " + (err.message || String(err)) + "\n");
  process.exit(1);
}

// The text_display body is the second child of root (header is first).
// Find a child whose innerHTML is non-empty — that's the body after
// setText(initial) ran.
let body = null;
for (const c of root.children) {
  if (c.innerHTML) { body = c; break; }
}
if (!body) {
  // Fall back to the second child even if empty (no initial content).
  body = root.children[1] || root.children[0] || makeNode("div");
}

process.stdout.write(body.innerHTML || "");
