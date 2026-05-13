// Extract structured fields embedded in a rendered block source string.
// mount_template.py injects `template: "<name>"` and `params: <json>` after
// the opening `({` so mobile (which can't eval JS) can resolve the registry
// entry and initial props without parsing the whole expression.

import type { GridCoords } from "../lib/grid/gridConfig";

export interface ParsedBlockSource {
  template: string | null;
  params: Record<string, unknown>;
  grid: GridCoords | null;
}

export function parseBlockSource(source: string): ParsedBlockSource {
  return {
    template: extractTemplate(source),
    params: extractJsonObject(source, "params") ?? {},
    grid: extractGrid(source),
  };
}

function extractTemplate(source: string): string | null {
  const m = source.match(/\btemplate:\s*"((?:[^"\\]|\\.)*)"/);
  if (!m) return null;
  try {
    return JSON.parse(`"${m[1]}"`);
  } catch {
    return m[1];
  }
}

function extractGrid(source: string): GridCoords | null {
  const m = source.match(/\bgrid:\s*\{\s*x:\s*(\d+)\s*,\s*y:\s*(\d+)\s*,\s*w:\s*(\d+)\s*,\s*h:\s*(\d+)/);
  if (!m) return null;
  return { x: Number(m[1]), y: Number(m[2]), w: Number(m[3]), h: Number(m[4]) };
}

// Find `<field>: { ... }` and return the parsed JSON object. Walks braces
// while respecting string literals + escapes so commas/braces inside string
// values don't confuse the matcher.
function extractJsonObject(source: string, field: string): Record<string, unknown> | null {
  const marker = new RegExp(`\\b${field}:\\s*\\{`);
  const m = marker.exec(source);
  if (!m) return null;
  const start = m.index + m[0].length - 1; // position of the opening `{`
  const end = findBalancedEnd(source, start);
  if (end < 0) return null;
  const slice = source.slice(start, end + 1);
  try {
    return JSON.parse(slice);
  } catch {
    return null;
  }
}

function findBalancedEnd(source: string, openIndex: number): number {
  let depth = 0;
  let inString = false;
  let stringChar = "";
  for (let i = openIndex; i < source.length; i++) {
    const c = source[i];
    if (inString) {
      if (c === "\\") { i++; continue; }
      if (c === stringChar) { inString = false; }
      continue;
    }
    if (c === '"' || c === "'") {
      inString = true;
      stringChar = c;
      continue;
    }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// Convert a kebab-case block id back to a snake_case template name. Used as a
// fallback when the source doesn't carry an explicit `template:` field (older
// backend renders, or future render formats that drop the embed).
export function templateFromBlockId(blockId: string): string {
  return blockId.replace(/-/g, "_");
}
