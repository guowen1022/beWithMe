export function isDesktop(): boolean {
  if (typeof window === "undefined") return false;
  return !!window.beWithMeBridge;
}

export function getBrowserBridge() {
  if (typeof window === "undefined") return null;
  return window.beWithMeBridge?.browser ?? null;
}
