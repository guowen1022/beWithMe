// Mirrors frontend/templates/blocks/url_card.md frontmatter. The card is
// stateless after mount — initial url/title/excerpt come in via params and
// don't update. Tapping opens the URL externally.

import type { BlockManifest } from "../blockRegistry";

export const urlCardManifest: BlockManifest = {
  name: "url_card",
  backend: {},
  publishes: [],
  subscribes: [],
};
