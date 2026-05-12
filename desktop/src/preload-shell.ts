import { contextBridge, ipcRenderer } from "electron";

console.log("[beWithMe] preload-shell loaded");

type Rect = { x: number; y: number; width: number; height: number };
type UrlPayload = { url: string; title: string };
type SelectionPayload = { text: string; url: string; title: string };
type LoadingPayload = { loading: boolean };
type ScrollPayload = {
  url: string;
  title: string;
  scroll_y: number;
  scroll_height: number;
  viewport_text: string;
};

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const listener = (_e: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

type ScreenSource = { id: string; name: string; kind: string };

contextBridge.exposeInMainWorld("beWithMeBridge", {
  browser: {
    navigate: (url: string) => ipcRenderer.invoke("browser:navigate", url),
    hide: () => ipcRenderer.invoke("browser:hide"),
    setBounds: (rect: Rect) => ipcRenderer.invoke("browser:set-bounds", rect),
    back: () => ipcRenderer.invoke("browser:back"),
    forward: () => ipcRenderer.invoke("browser:forward"),
    reload: () => ipcRenderer.invoke("browser:reload"),
    getCurrentUrl: () =>
      ipcRenderer.invoke("browser:current-url") as Promise<string | null>,
    onUrlChange: (cb: (p: UrlPayload) => void) =>
      subscribe<UrlPayload>("browser:url-changed", cb),
    onSelectionChange: (cb: (p: SelectionPayload) => void) =>
      subscribe<SelectionPayload>("browser:selection-changed", cb),
    onLoadingChange: (cb: (p: LoadingPayload) => void) =>
      subscribe<LoadingPayload>("browser:loading-changed", cb),
    onScrollChange: (cb: (p: ScrollPayload) => void) =>
      subscribe<ScrollPayload>("browser:scroll-changed", cb),
  },
  screen: {
    listSources: () =>
      ipcRenderer.invoke("screen:list_sources") as Promise<ScreenSource[]>,
  },
});
