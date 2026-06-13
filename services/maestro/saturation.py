"""Per-persona saturation — the feed's inter-source control knob (SPEC roadmap).

Saturation is *diminishing marginal benefit per persona*. Engagement is the
delivery mechanism for ALL personas (teacher, comforter, helper) — it is not
the enemy. We don't suppress engagement; we track how saturated each persona
is for this user and let that DIM the over-used persona's weight in the
blended feed, so other personas naturally rise. The feed self-regulates
without any "good vs bad engagement" arithmetic.

PHASE-0 STATUS: STUB. `persona_weight()` returns 1.0 for every persona — the
blend is degenerate while the teacher is the only live source, so a real
weight has nothing to act against yet. The interface is real, so wiring real
saturation later is a single-function change.

Future derivation (when ≥2 personas exist):
  - per-persona recent engagement time (turns/minutes attributed to the
    persona over a trailing window) → rising time ⇒ rising saturation
  - the feed-interaction signal (silicon_brain `inbox_interaction_log` /
    `user.card_selected` / `user.card_dismissed`): a persona whose cards keep
    getting selected is "being served well" (saturating); dismissed/expired
    cards push the other way
  - weight = f(1 - saturation), clamped to a floor so no persona is starved
"""
from __future__ import annotations

from uuid import UUID

from infra.silicon_brain_client import SiliconBrainClient


async def persona_weight(
    client: SiliconBrainClient, user_id: UUID, persona: str,
) -> float:
    """Return the inter-source blend weight for `persona`, in (0, 1].

    Phase-0 stub: always 1.0 (no dimming). Fill this in to make the feed
    saturation-aware — read per-persona engagement / interaction signals from
    the event stream via `client` and return a lower weight for over-used
    personas.
    """
    return 1.0
