export type DeviceClass = "phone" | "tablet" | "desktop";

// Hardcoded to phone in Phase 1. Tablet detection by Dimensions.width lands in Phase 2.
export function getDeviceClass(): DeviceClass {
  return "phone";
}
