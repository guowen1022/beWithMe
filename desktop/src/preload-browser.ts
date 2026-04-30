import { ipcRenderer } from "electron";

let lastSentText = "";
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

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
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(captureSelection, 150);
});

// Reset the dedupe cache across navigations so a selection of the same
// text on a new page still fires.
window.addEventListener("beforeunload", () => {
  lastSentText = "";
});
