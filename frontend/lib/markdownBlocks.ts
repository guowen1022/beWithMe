/**
 * Parse a markdown string into interactive logic blocks.
 *
 * The LLM is instructed to produce blocks separated by horizontal rules (---).
 * Each block starts with a **bold summary header** describing the step of
 * reasoning, followed by explanation paragraphs.
 *
 * Fallback: if no separators are found, splits on double-newline.
 *
 * The CONCEPTS: line at the end is stripped before rendering (it's only
 * consumed by the brain builder on the backend).
 */

export type LogicBlock = {
  id: string; // "block-0", "block-1", ...
  markdown: string; // raw markdown for this block (without CONCEPTS line)
  summary: string; // bold header text, for collapse display
};

/** Regex matching a separator line: ---, ***, === (3+ chars, optionally with spaces) */
const SEPARATOR_RE = /^[ \t]*[-*=]{3,}[ \t]*$/;

/** Regex to strip the trailing CONCEPTS: line */
const CONCEPTS_LINE_RE = /\n*\s*CONCEPTS:\s*[^\n]*\s*$/i;

/** Extract the bold summary header from a block: first **...** text. */
function extractBoldSummary(text: string): string {
  const match = text.match(/\*\*(.+?)\*\*/);
  if (match) return match[1];

  // Fallback: first sentence
  const plain = text.replace(/[#*`_~]/g, "").trim();
  for (let i = 0; i < Math.min(plain.length, 120); i++) {
    const ch = plain[i];
    if (ch === "." || ch === "!" || ch === "?") {
      const next = plain[i + 1];
      if (next === undefined || next === " " || next === "\n") {
        const candidate = plain.slice(0, i + 1);
        if (candidate.length >= 10) return candidate;
      }
    }
  }
  if (plain.length <= 80) return plain;
  const cut = plain.lastIndexOf(" ", 80);
  return plain.slice(0, cut > 20 ? cut : 80) + "...";
}

/**
 * Strip the CONCEPTS: line from the end of the markdown.
 * Returns the cleaned text (for UI display). The backend still has the
 * original with CONCEPTS for brain builder processing.
 */
export function stripConceptsLine(text: string): string {
  return text.replace(CONCEPTS_LINE_RE, "").trimEnd();
}

/**
 * Check if the text contains separator lines (---).
 * Used to decide between separator-based and fallback splitting.
 */
function hasSeparators(text: string): boolean {
  return text.split("\n").some((line) => SEPARATOR_RE.test(line));
}

/** Split text on separator lines into chunks. */
function splitOnSeparators(text: string): string[] {
  const lines = text.split("\n");
  const chunks: string[] = [];
  let current: string[] = [];

  for (const line of lines) {
    if (SEPARATOR_RE.test(line)) {
      if (current.length > 0) {
        chunks.push(current.join("\n").trim());
        current = [];
      }
    } else {
      current.push(line);
    }
  }
  if (current.length > 0) {
    const last = current.join("\n").trim();
    if (last) chunks.push(last);
  }
  return chunks;
}

/** Fallback: split on double-newline (for LLM output without separators). */
function splitOnParagraphs(text: string): string[] {
  return text
    .split(/\n\n+/)
    .map((c) => c.trim())
    .filter(Boolean);
}

export function parseMarkdownBlocks(markdown: string): LogicBlock[] {
  if (!markdown.trim()) return [];

  // Strip CONCEPTS line before parsing blocks
  const cleaned = stripConceptsLine(markdown);
  if (!cleaned.trim()) return [];

  // Choose splitting strategy
  const chunks = hasSeparators(cleaned)
    ? splitOnSeparators(cleaned)
    : splitOnParagraphs(cleaned);

  const blocks: LogicBlock[] = [];
  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (!chunk.trim()) continue;

    blocks.push({
      id: `block-${i}`,
      markdown: chunk,
      summary: extractBoldSummary(chunk),
    });
  }

  return blocks;
}
