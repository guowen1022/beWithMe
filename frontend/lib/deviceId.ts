// Per-device identity + capability detection.
//
// `device_id` is a stable UUID stored in localStorage. The same browser tab,
// the same browser across restarts, and the same browser after a backend
// restart all keep the same id — so the backend's `devices` table accumulates
// real per-device history rather than per-tab churn.
//
// Two tabs in the same browser share the device_id by design (they're the
// same physical surface). Two different browsers on the same laptop will get
// different device_ids — that's a deliberate trade-off; treating browsers as
// separate "devices" matches what the user can observe.

import type { DeviceClass } from "./device";

const DEVICE_ID_KEY = "bewithme_device_id";

export interface DeviceCapabilities {
  display: boolean;
  speaker: boolean;
  mic: boolean;
}

function uuidv4(): string {
  // Modern browsers ship crypto.randomUUID; fall back to a manual v4 for
  // older Safari builds where it might be missing.
  const c = (typeof crypto !== "undefined" ? crypto : null) as Crypto | null;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const bytes = new Uint8Array(16);
  (c ?? window.crypto).getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getDeviceId(): string {
  if (typeof window === "undefined") return "";
  let id = window.localStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = uuidv4();
    window.localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

const PHONE_MAX = 480;
const TABLET_MAX = 1024;

export function detectDeviceClass(): DeviceClass {
  if (typeof window === "undefined") return "desktop";
  const w = window.innerWidth;
  if (w <= PHONE_MAX) return "phone";
  if (w <= TABLET_MAX) return "tablet";
  return "desktop";
}

export function detectCapabilities(): DeviceCapabilities {
  if (typeof window === "undefined") {
    return { display: true, speaker: false, mic: false };
  }
  // The browser's mere existence implies a display. Speaker is assumed
  // present on every browser (Web Audio is universally available); the
  // user-consent step is what gates actual playback, not capability.
  // Mic presence is reported optimistically when mediaDevices is exposed —
  // we can't enumerate without a permission prompt, so this is "could ask"
  // rather than "is granted".
  const md = (navigator as Navigator).mediaDevices;
  return {
    display: true,
    speaker: true,
    mic: !!(md && typeof md.getUserMedia === "function"),
  };
}

export function deviceHeaders(): Record<string, string> {
  const id = getDeviceId();
  if (!id) return {};
  return {
    "X-Device-Id": id,
    "X-Device-Class": detectDeviceClass(),
    "X-Device-Capabilities": JSON.stringify(detectCapabilities()),
  };
}
