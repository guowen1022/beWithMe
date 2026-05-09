/**
 * web_view HTTP shim — exposes the Electron BrowserView to backend Python
 * sidecars over a token-authenticated localhost HTTP server.
 *
 * The persona doesn't run inside Electron — it lives in `services/browser`
 * as a Python sidecar. That sidecar resolves the port + token from the
 * registry file written here at startup, then POSTs to /open, /observe,
 * /click, etc. The shim wraps Electron's WebContents methods (loadURL,
 * sendInputEvent, executeJavaScript, capturePage) into a minimal verb set.
 *
 * Why HTTP and not Electron IPC: persona is out-of-process. Why not
 * Playwright over CDP: that needs --remote-debugging-port (open attack
 * surface) and target disambiguation between the shell window and the
 * BrowserView. The HTTP shim is one hop, no debug port, ~150 LOC.
 *
 * Auth: random 32-char hex token, required as `X-Web-View-Token`. Bound
 * to 127.0.0.1 only, so any local process with read access to the user-
 * data dir can drive the browser — that matches the trust boundary of
 * the python sidecars themselves.
 */
import * as http from "node:http";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { app, WebContentsView } from "electron";

interface ShimDeps {
  ensureBrowserView: () => WebContentsView;
  showBrowserView: () => void;
  hideBrowserView: () => void;
}

interface PerceptionReport {
  url: string;
  title: string;
  ready_state: string;
  loader_visible: boolean;
  video: {
    present: boolean;
    current_time?: number;
    duration?: number | null;
    paused?: boolean;
  };
  canvas: { present: boolean; count?: number };
  visible_text_excerpt: string;
  console_errors: { level: string; message: string }[];
  failed_requests: { url: string; error: string }[];
  screenshot_b64: string | null;
}

// Per-navigation buffers. /open clears them so each page starts fresh.
let consoleErrors: { level: string; message: string }[] = [];
let failedRequests: { url: string; error: string }[] = [];
let listenersAttached = false;

const PROBE_JS = `(() => {
  const loader = document.querySelector(
    '.loader, .loading, .spinner, [role="progressbar"], [aria-busy="true"]'
  );
  const v = document.querySelector('video');
  const cs = document.querySelectorAll('canvas');
  const text = (document.body && document.body.innerText || '')
    .replace(/\\s+/g, ' ')
    .trim()
    .slice(0, 400);
  return {
    ready_state: document.readyState,
    loader_visible: !!loader,
    video: v
      ? {
          present: true,
          current_time: v.currentTime,
          duration: isFinite(v.duration) ? v.duration : null,
          paused: v.paused,
        }
      : { present: false },
    canvas: { present: cs.length > 0, count: cs.length },
    visible_text_excerpt: text,
  };
})()`;

function readJsonBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function send(res: http.ServerResponse, status: number, body: any) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

async function observe(
  view: WebContentsView,
  includeScreenshot: boolean,
): Promise<PerceptionReport> {
  const wc = view.webContents;
  let probe: any;
  try {
    probe = await wc.executeJavaScript(PROBE_JS);
  } catch (e) {
    probe = { _probe_error: String(e) };
  }
  const report: PerceptionReport = {
    url: wc.getURL(),
    title: wc.getTitle(),
    ready_state: probe.ready_state || "unknown",
    loader_visible: !!probe.loader_visible,
    video: probe.video || { present: false },
    canvas: probe.canvas || { present: false },
    visible_text_excerpt: probe.visible_text_excerpt || "",
    console_errors: [...consoleErrors],
    failed_requests: [...failedRequests],
    screenshot_b64: null,
  };
  if (includeScreenshot) {
    const img = await wc.capturePage();
    report.screenshot_b64 = img.toPNG().toString("base64");
  }
  return report;
}

async function clickSelector(
  view: WebContentsView,
  selector: string,
): Promise<{ ok: boolean; reason?: string }> {
  const wc = view.webContents;
  const escaped = JSON.stringify(selector);
  const rect = await wc.executeJavaScript(`(() => {
    const el = document.querySelector(${escaped});
    if (!el) return null;
    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect();
    return {x: Math.floor(r.x + r.width/2), y: Math.floor(r.y + r.height/2)};
  })()`);
  if (!rect) return { ok: false, reason: "selector not found" };
  wc.sendInputEvent({
    type: "mouseDown",
    x: rect.x,
    y: rect.y,
    button: "left",
    clickCount: 1,
  } as any);
  wc.sendInputEvent({
    type: "mouseUp",
    x: rect.x,
    y: rect.y,
    button: "left",
    clickCount: 1,
  } as any);
  return { ok: true };
}

async function attachListeners(view: WebContentsView): Promise<void> {
  if (listenersAttached) return;
  const wc = view.webContents;
  // Electron's console-message signature is (event, level, message, line, source).
  // Levels: 0=verbose, 1=info, 2=warning, 3=error.
  wc.on("console-message", (_e: any, level: number, message: string) => {
    if (level >= 2) {
      consoleErrors.push({
        level: level === 3 ? "error" : "warning",
        message: String(message).slice(0, 500),
      });
      if (consoleErrors.length > 50) consoleErrors.shift();
    }
  });
  wc.session.webRequest.onErrorOccurred((details) => {
    // Skip aborts triggered by navigation; only keep network/auth failures.
    if (details.error === "net::ERR_ABORTED") return;
    failedRequests.push({ url: details.url, error: details.error });
    if (failedRequests.length > 50) failedRequests.shift();
  });
  listenersAttached = true;
}

async function waitForLoad(view: WebContentsView): Promise<void> {
  const wc = view.webContents;
  if (!wc.isLoading()) return;
  await new Promise<void>((resolve) => {
    const onStop = () => {
      wc.removeListener("did-stop-loading", onStop);
      resolve();
    };
    wc.on("did-stop-loading", onStop);
  });
}

export async function startWebViewShim(
  deps: ShimDeps,
): Promise<{ port: number; token: string }> {
  const token = crypto.randomBytes(16).toString("hex");

  const server = http.createServer(async (req, res) => {
    if (req.headers["x-web-view-token"] !== token) {
      return send(res, 401, { error: "unauthorized" });
    }
    if (req.method !== "POST") {
      return send(res, 405, { error: "method not allowed" });
    }
    let body: any;
    try {
      body = await readJsonBody(req);
    } catch (e: any) {
      return send(res, 400, { error: `bad json: ${e?.message || e}` });
    }
    const route = (req.url || "").replace(/\?.*/, "");
    try {
      switch (route) {
        case "/open": {
          if (!body.url) return send(res, 400, { error: "url required" });
          consoleErrors = [];
          failedRequests = [];
          const view = deps.ensureBrowserView();
          await attachListeners(view);
          deps.showBrowserView();
          await view.webContents.loadURL(String(body.url));
          await waitForLoad(view);
          return send(res, 200, await observe(view, !!body.include_screenshot));
        }
        case "/observe": {
          const view = deps.ensureBrowserView();
          await attachListeners(view);
          return send(res, 200, await observe(view, !!body.include_screenshot));
        }
        case "/click": {
          const view = deps.ensureBrowserView();
          if (body.selector) {
            const r = await clickSelector(view, String(body.selector));
            return send(res, r.ok ? 200 : 404, r);
          }
          if (typeof body.x === "number" && typeof body.y === "number") {
            const wc = view.webContents;
            wc.sendInputEvent({
              type: "mouseDown",
              x: body.x,
              y: body.y,
              button: "left",
              clickCount: 1,
            } as any);
            wc.sendInputEvent({
              type: "mouseUp",
              x: body.x,
              y: body.y,
              button: "left",
              clickCount: 1,
            } as any);
            return send(res, 200, { ok: true });
          }
          return send(res, 400, { error: "selector or {x,y} required" });
        }
        case "/type": {
          const view = deps.ensureBrowserView();
          const text = String(body.text || "");
          if (!text) return send(res, 400, { error: "text required" });
          if (body.selector) {
            const esc = JSON.stringify(String(body.selector));
            await view.webContents.executeJavaScript(`(() => {
              const el = document.querySelector(${esc});
              if (el && typeof el.focus === 'function') el.focus();
            })()`);
          }
          for (const ch of text) {
            view.webContents.sendInputEvent({
              type: "char",
              keyCode: ch,
            } as any);
          }
          return send(res, 200, { ok: true });
        }
        case "/scroll": {
          const view = deps.ensureBrowserView();
          const direction = body.direction === "up" ? -1 : 1;
          const amount = Number(body.amount) || 400;
          await view.webContents.executeJavaScript(
            `window.scrollBy(0, ${direction * amount})`,
          );
          return send(res, 200, { ok: true });
        }
        case "/wait_for": {
          const view = deps.ensureBrowserView();
          const selector = String(body.selector || "");
          if (!selector) return send(res, 400, { error: "selector required" });
          const timeoutMs = Number(body.timeout_ms) || 5000;
          const esc = JSON.stringify(selector);
          const found = await view.webContents.executeJavaScript(`new Promise((resolve) => {
            if (document.querySelector(${esc})) return resolve(true);
            let done = false;
            const obs = new MutationObserver(() => {
              if (document.querySelector(${esc})) {
                if (!done) { done = true; obs.disconnect(); resolve(true); }
              }
            });
            obs.observe(document.documentElement || document, {
              childList: true, subtree: true, attributes: true,
            });
            setTimeout(() => {
              if (!done) { done = true; obs.disconnect(); resolve(false); }
            }, ${timeoutMs});
          })`);
          return send(res, found ? 200 : 408, { ok: !!found });
        }
        case "/close": {
          deps.hideBrowserView();
          return send(res, 200, { ok: true });
        }
        default:
          return send(res, 404, { error: "not found" });
      }
    } catch (e: any) {
      return send(res, 500, { error: String(e?.message || e) });
    }
  });

  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        return reject(new Error("shim address resolution failed"));
      }
      const port = addr.port;
      // Resolve the registry path. Dev: <projectRoot>/data/web_view_port.json
      // (matches the data/browser_profile convention; both python sidecars
      // and Electron run from the same checkout). Prod: <userData>/...,
      // since the packaged .app has no writable project root. Env var
      // wins over both, so dev-desktop.sh can pin it explicitly if needed.
      const projectRoot = path.resolve(__dirname, "..", "..");
      const file =
        process.env.WEB_VIEW_PORT_FILE ||
        (app.isPackaged
          ? path.join(app.getPath("userData"), "web_view_port.json")
          : path.join(projectRoot, "data", "web_view_port.json"));
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(
          file,
          JSON.stringify({ port, token, pid: process.pid }, null, 2),
        );
      } catch (err) {
        return reject(err);
      }
      console.log(
        `[web_view_shim] listening on 127.0.0.1:${port} (registry: ${file})`,
      );
      // Best-effort cleanup so a stale registry from a crashed process can't
      // mislead the python sidecar after restart.
      app.on("will-quit", () => {
        try {
          fs.unlinkSync(file);
        } catch {
          // already gone — fine
        }
      });
      resolve({ port, token });
    });
  });
}
