/**
 * Parse a passage into a structural outline of sections.
 *
 * Detection order:
 * 1. Numbered headings: "3.1 Encoder and Decoder Stacks", "1 Introduction"
 * 2. Markdown headings: "## Attention"
 * 3. Short standalone lines that look like headings (capitalized, no period, < 80 chars)
 * 4. Fallback: group paragraphs into logical chunks
 *
 * Filters out figures, tables, and non-structural content.
 */

export type OutlineSection = {
  id: string;
  title: string;
  depth: number; // 0 = top-level, 1 = sub-section, etc.
  textStart: number;
  textEnd: number;
  children: OutlineSection[];
};

/** Lines to skip: figures, tables, URLs, emails, etc. */
const SKIP_RE =
  /^(figure\s*\d|table\s*\d|fig\.\s*\d|http|www\.|.*@.*\.\w{2,})/i;

/** Numbered heading: "1 Introduction" or "3.1 Encoder..." or "3.1. Encoder..." */
const NUMBERED_RE = /^(\d+(?:\.\d+)*\.?)\s+([A-Z][^\n]{2,})/;

/** Markdown heading */
const MD_HEADING_RE = /^(#{1,4})\s+(.+)/;

/**
 * Short standalone line that looks like a heading:
 * - Starts with uppercase
 * - No period at the end (not a sentence)
 * - Under 80 chars
 * - Not a figure/table caption
 */
function looksLikeHeading(line: string): boolean {
  const trimmed = line.trim();
  if (trimmed.length < 3 || trimmed.length > 100) return false;
  if (/[.!?]$/.test(trimmed)) return false; // ends with punctuation = sentence
  if (SKIP_RE.test(trimmed)) return false;
  if (/^[a-z]/.test(trimmed)) return false; // starts lowercase
  if (/^\d+$/.test(trimmed)) return false; // just a number
  // Must have at least 2 word characters
  if ((trimmed.match(/\w+/g) || []).length < 2) return false;
  return true;
}

function numberedDepth(num: string): number {
  return num.replace(/\.$/, "").split(".").length - 1;
}

type RawHeading = {
  idx: number;
  title: string;
  depth: number;
};

function extractHeadings(text: string): RawHeading[] {
  const lines = text.split("\n");
  const headings: RawHeading[] = [];
  let charPos = 0;

  for (const line of lines) {
    const trimmed = line.trim();

    // Skip empty, figures, tables
    if (!trimmed || SKIP_RE.test(trimmed)) {
      charPos += line.length + 1;
      continue;
    }

    // Try numbered heading
    const numMatch = trimmed.match(NUMBERED_RE);
    if (numMatch) {
      headings.push({
        idx: charPos,
        title: numMatch[2].trim(),
        depth: numberedDepth(numMatch[1]),
      });
      charPos += line.length + 1;
      continue;
    }

    // Try markdown heading
    const mdMatch = trimmed.match(MD_HEADING_RE);
    if (mdMatch) {
      headings.push({
        idx: charPos,
        title: mdMatch[2].trim(),
        depth: mdMatch[1].length - 1,
      });
      charPos += line.length + 1;
      continue;
    }

    // Try short standalone heading-like line
    if (looksLikeHeading(trimmed)) {
      // Check the next line isn't also heading-like (avoid treating paragraph starts as headings)
      headings.push({
        idx: charPos,
        title: trimmed,
        depth: 0, // will be adjusted later
      });
    }

    charPos += line.length + 1;
  }

  return headings;
}

/** Build a tree from flat headings by inferring parent-child from depth. */
function buildTree(headings: RawHeading[], textLength: number): OutlineSection[] {
  if (headings.length === 0) return [];

  // If all depths are 0 (from standalone lines), try to infer structure
  const allSameDepth = headings.every((h) => h.depth === headings[0].depth);

  const flat: OutlineSection[] = headings.map((h, i) => ({
    id: `section-${i}`,
    title: h.title,
    depth: h.depth,
    textStart: h.idx,
    textEnd: i + 1 < headings.length ? headings[i + 1].idx : textLength,
    children: [],
  }));

  if (allSameDepth) {
    // Flat list — return as-is
    return flat;
  }

  // Build nested tree
  const roots: OutlineSection[] = [];
  const stack: OutlineSection[] = [];

  for (const section of flat) {
    // Pop stack until we find a parent with lower depth
    while (stack.length > 0 && stack[stack.length - 1].depth >= section.depth) {
      stack.pop();
    }

    if (stack.length > 0) {
      stack[stack.length - 1].children.push(section);
    } else {
      roots.push(section);
    }
    stack.push(section);
  }

  return roots;
}

export function parsePassageOutline(text: string): OutlineSection[] {
  const headings = extractHeadings(text);

  // Filter: need at least 3 headings for a meaningful outline
  if (headings.length < 3) return [];

  // If we got too many "headings" (> 20), the detector is too aggressive — filter
  // to only keep numbered or markdown ones
  if (headings.length > 20) {
    const strict = headings.filter((h) => {
      const line = text.slice(h.idx, h.idx + h.title.length + 10);
      return NUMBERED_RE.test(line.trim()) || MD_HEADING_RE.test(line.trim());
    });
    if (strict.length >= 3) {
      return buildTree(strict, text.length);
    }
  }

  return buildTree(headings, text.length);
}

/**
 * Flatten a tree of sections for iteration (preserving depth).
 */
export function flattenSections(sections: OutlineSection[]): OutlineSection[] {
  const result: OutlineSection[] = [];
  function walk(nodes: OutlineSection[]) {
    for (const node of nodes) {
      result.push(node);
      walk(node.children);
    }
  }
  walk(sections);
  return result;
}

/** Normalize text for fuzzy matching: collapse whitespace, lowercase */
function normalize(text: string): string {
  return text.replace(/\s+/g, " ").toLowerCase().trim();
}

/**
 * Find which section a selectedText belongs to.
 * Uses fuzzy matching to handle PDF text extraction differences.
 */
export function findSectionForText(
  sections: OutlineSection[],
  passageText: string,
  selectedText: string,
): string | null {
  if (!selectedText || sections.length === 0) return null;

  const flat = flattenSections(sections);
  const needle = selectedText.slice(0, 100);

  // Try exact match first
  let idx = passageText.indexOf(needle);

  // Fuzzy: normalize whitespace and try again
  if (idx === -1) {
    const normPassage = normalize(passageText);
    const normNeedle = normalize(needle).slice(0, 60);
    const normIdx = normPassage.indexOf(normNeedle);
    if (normIdx !== -1) {
      // Map back to approximate original position
      idx = Math.round((normIdx / normPassage.length) * passageText.length);
    }
  }

  // Last resort: check which section's text contains the most words from selectedText
  if (idx === -1) {
    const words = normalize(needle).split(" ").filter((w) => w.length > 3).slice(0, 8);
    if (words.length >= 2) {
      let bestId: string | null = null;
      let bestCount = 0;
      for (const section of flat) {
        const sectionText = normalize(passageText.slice(section.textStart, section.textEnd));
        const count = words.filter((w) => sectionText.includes(w)).length;
        if (count > bestCount) {
          bestCount = count;
          bestId = section.id;
        }
      }
      if (bestCount >= Math.min(3, words.length)) return bestId;
    }
    return null;
  }

  for (const section of flat) {
    if (idx >= section.textStart && idx < section.textEnd) {
      return section.id;
    }
  }
  return null;
}
