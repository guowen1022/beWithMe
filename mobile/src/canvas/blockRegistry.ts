// Maps a template name (e.g. "ambient_mic") to a native RN component + its
// manifest. This is the workaround for RN's lack of runtime eval: web blocks
// are JS evaluated at mount time; mobile blocks are compiled TS resolved by
// name from this registry.
//
// Phase 1 has one entry: ambient_mic. Phase 2+ adds more files in
// canvas/blocks/ and an entry here. Nothing else changes.

import type React from "react";
import type { GridCoords } from "../lib/grid/gridConfig";
import { AmbientMicBlock } from "./blocks/AmbientMicBlock";
import { ambientMicManifest } from "./blocks/AmbientMicBlock.manifest";
import { RichCardBlock } from "./blocks/RichCardBlock";
import { richCardManifest } from "./blocks/RichCardBlock.manifest";
import { TextDisplayBlock } from "./blocks/TextDisplayBlock";
import { textDisplayManifest } from "./blocks/TextDisplayBlock.manifest";
import { UrlCardBlock } from "./blocks/UrlCardBlock";
import { urlCardManifest } from "./blocks/UrlCardBlock.manifest";

export interface BackendCallSpec {
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  path: string;
  auth?: "user" | "public";
  content_type?: "application/json" | "multipart/form-data";
  returns?: "json" | "text" | "blob";
}

export interface BlockManifest {
  name: string;
  backend: Record<string, BackendCallSpec>;
  publishes: string[];
  subscribes: string[];
}

export type BackendResult = { ok: boolean; status: number; data: unknown };
export type BackendCaller = (args?: Record<string, unknown> | FormData) => Promise<BackendResult>;

export interface BlockProps {
  blockId: string;
  grid: GridCoords;
  params?: Record<string, unknown>;
}

export interface RegistryEntry {
  component: React.ComponentType<BlockProps>;
  manifest: BlockManifest;
  // Phase 1: per-device override of the desktop grid coords. Mobile gets the
  // full canvas; desktop keeps the small bottom-right placement.
  mobileGrid?: GridCoords;
}

export const blockRegistry: Record<string, RegistryEntry> = {
  ambient_mic: {
    component: AmbientMicBlock,
    manifest: ambientMicManifest,
    // Bottom strip on phone — mirrors the desktop bottom-right placement and
    // leaves the top of the canvas free for blocks the persona mounts.
    mobileGrid: { x: 0, y: 7, w: 4, h: 2 },
  },
  text_display: {
    component: TextDisplayBlock,
    manifest: textDisplayManifest,
  },
  rich_card: {
    component: RichCardBlock,
    manifest: richCardManifest,
  },
  url_card: {
    component: UrlCardBlock,
    manifest: urlCardManifest,
  },
};

export function resolveBlock(templateName: string): RegistryEntry | undefined {
  return blockRegistry[templateName];
}
