// Mirrors frontend/templates/blocks/text_display.md frontmatter. The persona
// pushes prose updates via push_block_content on the per-block content topic.

import type { BlockManifest } from "../blockRegistry";

export const textDisplayManifest: BlockManifest = {
  name: "text_display",
  backend: {},
  publishes: ["text.selected"],
  subscribes: ["text.<block_id>.content"],
};
