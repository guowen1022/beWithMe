// Mirrors frontend/templates/blocks/rich_card.md frontmatter. Persona
// pushes HTML updates via push_block_content on the per-block content
// topic; the backend re-runs the rich_card preprocessor (sanitize +
// diagram resolve) before fan-out, so the value arriving here is
// already trusted HTML+SVG.

import type { BlockManifest } from "../blockRegistry";

export const richCardManifest: BlockManifest = {
  name: "rich_card",
  backend: {},
  publishes: ["text.selected"],
  subscribes: ["text.<block_id>.content"],
};
