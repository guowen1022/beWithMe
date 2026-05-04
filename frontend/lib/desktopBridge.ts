import { postBlockState } from "./blockState";

export function isDesktop(): boolean {
  if (typeof window === "undefined") return false;
  return !!window.beWithMeBridge;
}

export function getBrowserBridge() {
  if (typeof window === "undefined") return null;
  return window.beWithMeBridge?.browser ?? null;
}

// ---------------------------------------------------------------------------
// Perception bridge: turn Electron browser-view observations into
// state reports for the persona's read_media tool.
//
// The Electron preload running inside `browserView` posts URL changes,
// selection changes, and scroll events to the main process. main.ts
// forwards them to this shell view. We aggregate them into one logical
// `desktop-browser` block whose state the persona can read like any
// other block.
// ---------------------------------------------------------------------------

const DESKTOP_BROWSER_BLOCK_ID = "desktop-browser";

interface BrowserPerceptionState {
  url: string;
  title: string;
  scroll_y: number;
  scroll_height: number;
  viewport_text: string;
  selection: string;
  loading: boolean;
}

const perceptionState: BrowserPerceptionState = {
  url: "",
  title: "",
  scroll_y: 0,
  scroll_height: 0,
  viewport_text: "",
  selection: "",
  loading: false,
};

let perceptionInstalled = false;
let perceptionUnsubs: Array<() => void> = [];

function summarizeBrowser(): string {
  const head = perceptionState.title || perceptionState.url || "(no page)";
  if (perceptionState.viewport_text) {
    const snippet = perceptionState.viewport_text.split("\n", 1)[0]?.slice(0, 200) ?? "";
    return `${head} — ${snippet}`;
  }
  return head;
}

function publishBrowserState(): void {
  if (!perceptionState.url) return;
  postBlockState(DESKTOP_BROWSER_BLOCK_ID, {
    kind: "browser",
    content: summarizeBrowser(),
    // The Electron browserView always carries user attention when it's
    // visible — there's no canvas focus competition. Mark active whenever
    // the URL has been set; later we can distinguish hidden vs shown.
    focus: "active",
    extra: {
      url: perceptionState.url,
      title: perceptionState.title,
      scroll_y: perceptionState.scroll_y,
      scroll_height: perceptionState.scroll_height,
      viewport_text: perceptionState.viewport_text,
      selection: perceptionState.selection,
      loading: perceptionState.loading,
    },
  });
}

/** Install the perception bridge. Idempotent; safe to call from a React effect. */
export function installDesktopBrowserPerception(): void {
  if (perceptionInstalled) return;
  const browser = getBrowserBridge();
  if (!browser) return;
  perceptionInstalled = true;

  perceptionUnsubs.push(browser.onUrlChange((p) => {
    perceptionState.url = p.url;
    perceptionState.title = p.title;
    // URL change resets per-page state.
    perceptionState.scroll_y = 0;
    perceptionState.viewport_text = "";
    perceptionState.selection = "";
    publishBrowserState();
  }));

  perceptionUnsubs.push(browser.onSelectionChange((p) => {
    perceptionState.selection = p.text;
    perceptionState.url = p.url || perceptionState.url;
    perceptionState.title = p.title || perceptionState.title;
    publishBrowserState();
  }));

  perceptionUnsubs.push(browser.onScrollChange((p) => {
    perceptionState.url = p.url;
    perceptionState.title = p.title;
    perceptionState.scroll_y = p.scroll_y;
    perceptionState.scroll_height = p.scroll_height;
    perceptionState.viewport_text = p.viewport_text;
    publishBrowserState();
  }));

  perceptionUnsubs.push(browser.onLoadingChange((p) => {
    perceptionState.loading = p.loading;
    // No publish here — loading-only state isn't worth a POST. The next
    // URL/scroll/selection event carries the freshest loading flag.
  }));

  // Seed with the current URL if the browser was already open.
  browser.getCurrentUrl().then((url) => {
    if (url && !perceptionState.url) {
      perceptionState.url = url;
      publishBrowserState();
    }
  }).catch(() => { /* ignored */ });
}

export function uninstallDesktopBrowserPerception(): void {
  for (const u of perceptionUnsubs) {
    try { u(); } catch { /* ignored */ }
  }
  perceptionUnsubs = [];
  perceptionInstalled = false;
}
