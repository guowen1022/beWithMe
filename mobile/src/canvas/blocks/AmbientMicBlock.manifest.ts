// Mirrors frontend/templates/blocks/ambient_mic.md frontmatter. Declares the
// backend calls the block needs (used by helpers.backend.<name>) and the
// bus topics it participates in.

import type { BlockManifest } from "../blockRegistry";

export const ambientMicManifest: BlockManifest = {
  name: "ambient_mic",
  backend: {
    recordUtterance: {
      method: "POST",
      path: "/api/perception/utterance",
      auth: "user",
      content_type: "application/json",
      returns: "json",
    },
    transcribe: {
      method: "POST",
      path: "/api/transcribe",
      auth: "user",
      content_type: "multipart/form-data",
      returns: "json",
    },
  },
  publishes: ["ambient_mic.muted", "ambient_mic.speak_to"],
  subscribes: [],
};
