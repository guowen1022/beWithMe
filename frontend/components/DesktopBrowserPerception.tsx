"use client";

import { useEffect } from "react";
import {
  installDesktopBrowserPerception,
  uninstallDesktopBrowserPerception,
} from "@/lib/desktopBridge";

/**
 * Mounts once at the root. When running inside the Electron shell, hooks
 * the browserView's URL/selection/scroll observers and republishes their
 * payloads as state on the synthetic block id `desktop-browser`. The
 * persona reads it via read_media like any other block.
 *
 * No-op in plain web (no window.beWithMeBridge).
 */
export default function DesktopBrowserPerception() {
  useEffect(() => {
    installDesktopBrowserPerception();
    return () => uninstallDesktopBrowserPerception();
  }, []);
  return null;
}
