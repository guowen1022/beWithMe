// Detects device class from window width. Blocks read `data-device` from
// their parent canvas (set by DynamicSurface) and restyle themselves —
// it's the smallest CSS-variant story without committing to a real
// multi-window/multi-monitor model.

import { useEffect, useState } from "react";

export type DeviceClass = "phone" | "tablet" | "desktop";

const PHONE_MAX = 480;
const TABLET_MAX = 1024;

function classify(width: number): DeviceClass {
  if (width <= PHONE_MAX) return "phone";
  if (width <= TABLET_MAX) return "tablet";
  return "desktop";
}

export function useDeviceClass(): DeviceClass {
  const [device, setDevice] = useState<DeviceClass>("desktop");

  useEffect(() => {
    const update = () => setDevice(classify(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return device;
}
