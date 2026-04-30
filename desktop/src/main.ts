import {
  app,
  BaseWindow,
  WebContentsView,
  ipcMain,
  nativeTheme,
  session,
} from "electron";
import path from "node:path";
import fs from "node:fs";

// The web app is designed for a single light palette. Force Electron to
// always render in light mode so the window title bar doesn't pick up the
// user's system dark mode (which would produce white-on-dark titles while
// the web content stays light).
nativeTheme.themeSource = "light";

const DEV = process.env.BEWITHME_DEV === "1";
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
  if (lastBrowserBounds) view.setBounds(lastBrowserBounds);
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

  if (DEV) {
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
}

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

function configurePermissions() {
  const allowed = new Set(["media", "mediaKeySystem", "clipboard-read"]);
  session.defaultSession.setPermissionRequestHandler(
    (_wc, permission, callback) => callback(allowed.has(permission)),
  );
  session.defaultSession.setPermissionCheckHandler((_wc, permission) =>
    allowed.has(permission),
  );
}

app.whenReady().then(() => {
  configurePermissions();
  createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (!mainWindow) createWindow();
});
