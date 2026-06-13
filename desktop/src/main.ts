import {
  app,
  BaseWindow,
  WebContentsView,
  desktopCapturer,
  ipcMain,
  nativeTheme,
  session,
  systemPreferences,
} from "electron";
import path from "node:path";
import fs from "node:fs";
import { startWebViewShim } from "./web_view_shim";

// The web app is designed for a single light palette. Force Electron to
// always render in light mode so the window title bar doesn't pick up the
// user's system dark mode (which would produce white-on-dark titles while
// the web content stays light).
nativeTheme.themeSource = "light";

const DEV = process.env.BEWITHME_DEV === "1";
// Master switch for developer debug surfaces (shared with the frontend's
// NEXT_PUBLIC_BEWITHME_DEBUG; both fanned out from BEWITHME_DEBUG by
// scripts/dev-desktop.sh). Default ON — only an explicit "0" disables.
// Here it gates the detached Chromium DevTools window.
const DEBUG = process.env.BEWITHME_DEBUG !== "0";
const SHELL_URL = process.env.SHELL_URL || "http://localhost:3000/";

type Rect = { x: number; y: number; width: number; height: number };

interface WindowState {
  width: number;
  height: number;
  x?: number;
  y?: number;
}

const stateFile = () => path.join(app.getPath("userData"), "window.json");

function loadState(): WindowState {
  try {
    return JSON.parse(fs.readFileSync(stateFile(), "utf8"));
  } catch {
    return { width: 1400, height: 900 };
  }
}

function saveState(win: BaseWindow) {
  const bounds = win.getBounds();
  try {
    fs.writeFileSync(stateFile(), JSON.stringify(bounds));
  } catch (err) {
    console.error("failed to persist window state", err);
  }
}

let mainWindow: BaseWindow | null = null;
let shellView: WebContentsView | null = null;
let browserView: WebContentsView | null = null;
let browserVisible = false;
let lastBrowserBounds: Rect | null = null;

function fitShellToWindow(win: BaseWindow, view: WebContentsView) {
  const { width, height } = win.getContentBounds();
  view.setBounds({ x: 0, y: 0, width, height });
}

function clampBounds(win: BaseWindow, rect: Rect): Rect {
  const { width: W, height: H } = win.getContentBounds();
  const x = Math.max(0, Math.min(rect.x, W));
  const y = Math.max(0, Math.min(rect.y, H));
  const width = Math.max(0, Math.min(rect.width, W - x));
  const height = Math.max(0, Math.min(rect.height, H - y));
  return { x, y, width, height };
}

function ensureBrowserView(): WebContentsView {
  if (browserView) return browserView;

  const preload = path.join(__dirname, "preload-browser.js");
  browserView = new WebContentsView({
    webPreferences: {
      preload,
      partition: "persist:bewithme-browser",
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const wc = browserView.webContents;

  const pushUrl = () => {
    shellView?.webContents.send("browser:url-changed", {
      url: wc.getURL(),
      title: wc.getTitle(),
    });
  };
  wc.on("did-navigate", pushUrl);
  wc.on("did-navigate-in-page", pushUrl);
  wc.on("page-title-updated", pushUrl);
  wc.on("did-start-loading", () => {
    shellView?.webContents.send("browser:loading-changed", { loading: true });
  });
  wc.on("did-stop-loading", () => {
    shellView?.webContents.send("browser:loading-changed", { loading: false });
  });

  wc.setWindowOpenHandler(({ url }) => {
    wc.loadURL(url).catch((err) => console.warn("popup loadURL failed", err));
    return { action: "deny" };
  });

  return browserView;
}

function showBrowserView(win: BaseWindow, view: WebContentsView) {
  if (!browserVisible) {
    win.contentView.addChildView(view);
    browserVisible = true;
  }
  // Without bounds the BrowserView is added to the tree but has zero size,
  // so the page loads invisibly. The frontend's existing "browse" UI sets
  // bounds via browser:set-bounds, but persona-driven web_view calls may
  // happen before the user has ever opened that UI. Default to a centered
  // ~80% rectangle so the user actually sees the page.
  if (!lastBrowserBounds) {
    const { width: W, height: H } = win.getContentBounds();
    lastBrowserBounds = clampBounds(win, {
      x: Math.floor(W * 0.1),
      y: Math.floor(H * 0.1),
      width: Math.floor(W * 0.8),
      height: Math.floor(H * 0.8),
    });
  }
  view.setBounds(lastBrowserBounds);
}

function hideBrowserView(win: BaseWindow, view: WebContentsView) {
  if (!browserVisible) return;
  win.contentView.removeChildView(view);
  browserVisible = false;
}

function createWindow() {
  const state = loadState();
  const win = new BaseWindow({
    ...state,
    title: "beWithMe",
    backgroundColor: "#f9fafb",
  });
  mainWindow = win;

  const shellPreload = path.join(__dirname, "preload-shell.js");
  const view = new WebContentsView({
    webPreferences: {
      preload: shellPreload,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  shellView = view;

  win.contentView.addChildView(view);
  fitShellToWindow(win, view);
  view.webContents.loadURL(SHELL_URL).catch((err) =>
    console.error("shell loadURL failed", err),
  );

  if (DEV && DEBUG) {
    view.webContents.openDevTools({ mode: "detach" });
  }

  win.on("resize", () => {
    fitShellToWindow(win, view);
    if (lastBrowserBounds && browserView && browserVisible) {
      browserView.setBounds(clampBounds(win, lastBrowserBounds));
    }
    saveState(win);
  });
  win.on("move", () => saveState(win));
  win.on("close", () => saveState(win));
  // Clear our refs so the dock-icon `activate` handler can re-create the
  // window. Without this, `mainWindow` still points at a destroyed
  // BrowserWindow and `if (!mainWindow)` is false → nothing reopens.
  win.on("closed", () => {
    if (mainWindow === win) {
      mainWindow = null;
      shellView = null;
      // browserView lives inside mainWindow's contentView, so closing
      // the window also tears it down. Drop the refs to match reality.
      browserView = null;
      browserVisible = false;
    }
  });
}

// Screen-share: enumerate displays + windows the renderer can capture.
// The frontend `screen_share` block calls this, then passes the chosen
// `id` to `getUserMedia({video: {mandatory: {chromeMediaSourceId: id}}})`.
ipcMain.handle("screen:list_sources", async () => {
  const sources = await desktopCapturer.getSources({
    types: ["screen", "window"],
    thumbnailSize: { width: 0, height: 0 }, // skip thumbnails — we don't render them yet
    fetchWindowIcons: false,
  });
  return sources.map((s) => ({
    id: s.id,
    name: s.name,
    kind: s.id.split(":")[0], // "screen" or "window"
  }));
});

ipcMain.handle("browser:navigate", async (_e, url: string) => {
  if (!mainWindow) return;
  const view = ensureBrowserView();
  showBrowserView(mainWindow, view);
  await view.webContents.loadURL(url);
});

ipcMain.handle("browser:hide", () => {
  if (mainWindow && browserView) hideBrowserView(mainWindow, browserView);
});

ipcMain.handle("browser:set-bounds", (_e, rect: Rect) => {
  if (!mainWindow) return;
  const clamped = clampBounds(mainWindow, rect);
  lastBrowserBounds = clamped;
  if (browserView && browserVisible) browserView.setBounds(clamped);
});

ipcMain.handle("browser:back", () => {
  const wc = browserView?.webContents;
  if (wc?.canGoBack()) wc.goBack();
});

ipcMain.handle("browser:forward", () => {
  const wc = browserView?.webContents;
  if (wc?.canGoForward()) wc.goForward();
});

ipcMain.handle("browser:reload", () => {
  browserView?.webContents.reload();
});

ipcMain.handle("browser:current-url", () => {
  return browserView?.webContents.getURL() ?? null;
});

ipcMain.on(
  "browser:selection-raw",
  (_e, payload: { text: string; url: string; title: string }) => {
    shellView?.webContents.send("browser:selection-changed", payload);
  },
);

ipcMain.on(
  "browser:scroll-raw",
  (
    _e,
    payload: {
      url: string;
      title: string;
      scroll_y: number;
      scroll_height: number;
      viewport_text: string;
    },
  ) => {
    shellView?.webContents.send("browser:scroll-changed", payload);
  },
);

function configurePermissions() {
  const allowed = new Set(["media", "mediaKeySystem", "clipboard-read"]);
  session.defaultSession.setPermissionRequestHandler(
    (_wc, permission, callback) => callback(allowed.has(permission)),
  );
  session.defaultSession.setPermissionCheckHandler((_wc, permission) =>
    allowed.has(permission),
  );
}

async function ensureMacMediaPermissions(): Promise<void> {
  // macOS-only: Chromium's permission allowlist (configurePermissions
  // above) is not enough — the *OS* itself must also have granted the
  // app access to the microphone, otherwise renderer getUserMedia()
  // calls fail with NotFoundError (Chromium maps "OS-denied" to that).
  // Trigger the system prompt the first time the user opens the app;
  // returns immediately on already-granted, no-op on non-mac.
  if (process.platform !== "darwin") return;
  try {
    const t0 = Date.now();
    const status = systemPreferences.getMediaAccessStatus("microphone");
    console.log(
      `[main] mic TCC status: ${status} (check took ${Date.now() - t0}ms)`
    );
    if (status !== "granted") {
      const tPrompt = Date.now();
      const granted = await systemPreferences.askForMediaAccess("microphone");
      console.log(
        `[main] mic access ${granted ? "granted" : "DENIED"} after ${Date.now() - tPrompt}ms`
      );
    }
  } catch (err) {
    console.warn("[main] mic access prompt failed:", err);
  }
  // Screen recording can't be requested programmatically on macOS — the
  // user has to flip the toggle in System Settings themselves. Detect
  // and surface the status in logs so the dev knows why a black screen
  // shows up if it does.
  try {
    const screenStatus = systemPreferences.getMediaAccessStatus("screen");
    if (screenStatus !== "granted") {
      console.warn(
        `[main] screen-recording access is "${screenStatus}". ` +
          "Open System Settings → Privacy & Security → Screen Recording " +
          "and enable beWithMe / Electron if screen_share shows a black frame."
      );
    }
  } catch {}
}

app.whenReady().then(async () => {
  configurePermissions();
  await ensureMacMediaPermissions();
  createWindow();
  // HTTP shim for the persona's web_view tool. Writes port+token to
  // <userData>/web_view_port.json so the Python sidecar can find it.
  try {
    await startWebViewShim({
      ensureBrowserView,
      showBrowserView: () => {
        if (!mainWindow) return;
        showBrowserView(mainWindow, ensureBrowserView());
      },
      hideBrowserView: () => {
        if (!mainWindow || !browserView) return;
        hideBrowserView(mainWindow, browserView);
      },
    });
  } catch (err) {
    console.error("[web_view_shim] failed to start:", err);
  }
});

// Close-the-window = quit-the-app. macOS convention is to keep apps in the
// dock after the last window closes, but for this dev shell that creates
// confusing pile-ups (the user sees three Electron icons after three
// dev-desktop runs). Quit eagerly so dock state mirrors what's actually
// running.
app.on("window-all-closed", () => {
  app.quit();
});

app.on("activate", () => {
  if (!mainWindow) createWindow();
});
