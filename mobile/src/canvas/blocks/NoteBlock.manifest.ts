// Mirrors frontend/templates/blocks/note.md frontmatter. Persona
// pushes HTML updates via push_block_content on the per-block content
// topic; the backend re-runs the note preprocessor (sanitize +
// diagram resolve) before fan-out, so the value arriving here is
// already trusted HTML+SVG.

import type { BlockManifest } from "../blockRegistry";

export const noteManifest: BlockManifest = {
  name: "note",
  backend: {},
  publishes: ["text.selected"],
  subscribes: ["text.<block_id>.content"],
};
