// List the user's known devices for the output-routing picker.

import { api } from "./client";

export interface DeviceSummary {
  id: string;
  device_class: "phone" | "tablet" | "desktop";
  online: boolean;
  last_seen: string | null;
  first_seen: string | null;
}

export async function listDevices(): Promise<DeviceSummary[]> {
  const res = await api("/api/dynamic/devices", { method: "GET" });
  if (!res.ok) throw new Error(`listDevices failed (${res.status})`);
  const body = await res.json() as { devices: DeviceSummary[] };
  return body.devices ?? [];
}
