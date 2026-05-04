import { ipcRenderer } from "electron";

// ---- Selection capture (existing) -----------------------------------------

let lastSentText = "";
let selectionTimer: ReturnType<typeof setTimeout> | null = null;

function captureSelection() {
  const selection = window.getSelection();
  const text = selection?.toString().trim() ?? "";
  if (!text || text === lastSentText) return;
  lastSentText = text;
  ipcRenderer.send("browser:selection-raw", {
    text,
    url: window.location.href,
    title: document.title,
  });
}

document.addEventListener("selectionchange", () => {
  if (selectionTimer) clearTimeout(selectionTimer);
  selectionTimer = setTimeout(captureSelection, 150);
});

// ---- Scroll + viewport observability (perception) -------------------------
// On every scroll (debounced 250 ms) we send the current scroll position
// plus a snippet of viewport text — the paragraph-level elements currently
// intersecting the viewport, concatenated and capped. Lets the persona's
// read_media tool answer "what is the user reading right now?" The shell
// process forwards this into the perception cache as a `desktop-browser`
// block state report.

const VIEWPORT_TEXT_MAX = 1500;
const SCROLL_DEBOUNCE_MS = 250;
let scrollTimer: ReturnType<typeof setTimeout> | null = null;

function gatherViewportText(): string {
  const candidates = document.querySelectorAll(
    "p, h1, h2, h3, h4, h5, h6, li, blockquote, pre, article, section",
  );
  const innerH = window.innerHeight || document.documentElement.clientHeight;
  const innerW = window.innerWidth || document.documentElement.clientWidth;
  const pieces: string[] = [];
  let total = 0;
  for (const el of Array.from(candidates)) {
    const rect = (el as HTMLElement).getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > innerH) continue;
    if (rect.right < 0 || rect.left > innerW) continue;
    const text = ((el as HTMLElement).innerText || "").trim();
    if (text.length < 40) continue;   // filter UI chrome / icon labels
    pieces.push(text);
    total += text.length + 2;
    if (total > VIEWPORT_TEXT_MAX * 1.5) break;
  }
  let joined = pieces.join("\n\n");
  if (joined.length > VIEWPORT_TEXT_MAX) {
    joined = joined.slice(0, VIEWPORT_TEXT_MAX) + "…";
  }
  return joined;
}

function captureScroll() {
  ipcRenderer.send("browser:scroll-raw", {
    url: window.location.href,
    title: document.title,
    scroll_y: window.scrollY,
    scroll_height: document.documentElement.scrollHeight,
    viewport_text: gatherViewportText(),
  });
}

document.addEventListener(
  "scroll",
  () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(captureScroll, SCROLL_DEBOUNCE_MS);
  },
  { passive: true, capture: true },
);

// Initial snapshot once the page is interactive — covers the first load
// of any URL where the user lands without scrolling.
window.addEventListener("load", () => {
  if (scrollTimer) clearTimeout(scrollTimer);
  scrollTimer = setTimeout(captureScroll, SCROLL_DEBOUNCE_MS);
});

// ---- Cleanup across navigations -------------------------------------------

window.addEventListener("beforeunload", () => {
  lastSentText = "";
});
