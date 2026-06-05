"""Top-K candidate generation + diversity dedup (SPEC §6.1).

Two layers per the SPEC:

  1. **Inside the LLM call.** Prompt instructs explicit diversity along at
     least one of (concept thread, action shape, time scope, posture).
  2. **Downstream dedup.** Cosine-similarity check over text embeddings;
     pairs above `DEDUP_THRESHOLD` (0.85) collapse to the higher-prior
     winner.

Phase 0 K range: [1, 3]. K=1 is allowed and common; K is an upper bound,
not a target. The Maestro is encouraged to silence when confidence is
low rather than pad the list.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional

from infra.model import llm as _llm
from infra.rag.embedding import embed_batch


# SPEC §6.1.3 — 0.85 cosine similarity per IMPLEMENTATION.md §6.8.
DEDUP_THRESHOLD = 0.85

# K bounds (SPEC §6.1).
MAX_K = 3


@dataclass
class Candidate:
    """One ACT candidate the agent can realize as an inbox proposal."""

    title: str
    posture: str            # SPEC §5.7 valid posture name
    persona_purpose: str    # e.g. teacher:long-horizon-propose
    action_shape: str       # e.g. inbox-proposal, voice_message, surface_capture
    opening: str            # short paragraph (the cache substrate for PR-5)
    prior: float            # Maestro's confidence in this candidate (0..1)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "posture": self.posture,
            "persona_purpose": self.persona_purpose,
            "action_shape": self.action_shape,
            "opening": self.opening,
            "prior": self.prior,
        }


_PROMPT_TMPL = """\
You are the Maestro long instance — a longitudinal reading-companion planner.
You are NOT writing for the user; you are writing PROPOSALS the agent can
realize as inbox items. Default to FEW high-quality candidates over many
mediocre ones. Top-K is an upper bound, not a target.

Substrate (everything you know about this learner right now):

{slice_text}

Generate up to {k} DIVERSE candidates. Diversity requirement: each candidate
must differ from the others on at least ONE of these axes:
  - concept thread (what topic / area)
  - action shape (proposal kind: inbox-proposal | voice_message | surface_capture)
  - time scope (short reinforcement / next reachable concept / long-horizon stretch)
  - posture (steady | deepen | pivot | hold | wind_down)

If the substrate doesn't support {k} *meaningfully different* candidates,
return FEWER. Single-candidate ACT is fine. ZERO candidates ("silence is
better") is acceptable — return an empty list.

Output strict JSON of the shape:
[
  {{
    "title": "short, distinct label",
    "posture": "steady|deepen|pivot|hold|wind_down|escalate|interrupt_now",
    "persona_purpose": "teacher:<short-slug>",
    "action_shape": "inbox-proposal|voice_message|surface_capture",
    "opening": "1-3 sentences the agent will turn into the proposal text",
    "prior": 0.0
  }},
  ...
]

Return ONLY the JSON array. No prose, no markdown fences."""


_VALID_POSTURES = {
    "steady", "deepen", "pivot", "hold",
    "wind_down", "escalate", "interrupt_now",
}
_VALID_ACTION_SHAPES = {
    "inbox-proposal", "voice_message", "surface_capture",
}


def _coerce_candidate(raw: dict) -> Optional[Candidate]:
    """Defensive parse — drop anything that fails validation."""
    try:
        title = str(raw["title"]).strip()
        posture = str(raw["posture"]).strip()
        persona_purpose = str(raw["persona_purpose"]).strip()
        action_shape = str(raw["action_shape"]).strip()
        opening = str(raw["opening"]).strip()
        prior = float(raw.get("prior", 0.5))
    except (KeyError, TypeError, ValueError):
        return None
    if not title or not opening:
        return None
    if posture not in _VALID_POSTURES:
        return None
    if action_shape not in _VALID_ACTION_SHAPES:
        return None
    if not persona_purpose:
        return None
    return Candidate(
        title=title,
        posture=posture,
        persona_purpose=persona_purpose,
        action_shape=action_shape,
        opening=opening,
        prior=max(0.0, min(1.0, prior)),
    )


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _dedup(candidates: list[Candidate]) -> list[Candidate]:
    """Drop near-duplicates by cosine similarity over the opening text.

    Best-effort: if embedding fails (Ollama unreachable), skip dedup and
    return the input — Phase 0 prefers shipping over correctness when
    embeddings aren't available.
    """
    if len(candidates) <= 1:
        return candidates
    try:
        vectors = await embed_batch([c.opening for c in candidates])
    except Exception:
        return candidates

    kept: list[Candidate] = []
    kept_vecs: list[list[float]] = []
    # Highest prior first — when two are similar, keep the more confident one.
    order = sorted(range(len(candidates)), key=lambda i: -candidates[i].prior)
    for idx in order:
        c = candidates[idx]
        v = vectors[idx]
        duplicate_of = None
        for k_i, kv in enumerate(kept_vecs):
            if _cosine(v, kv) >= DEDUP_THRESHOLD:
                duplicate_of = k_i
                break
        if duplicate_of is None:
            kept.append(c)
            kept_vecs.append(v)
    return kept


async def generate(slice_text: str, *, k: int = MAX_K, user_id=None) -> list[Candidate]:
    """LLM call → dedup → trim to k. Returns 0..k candidates."""
    k = max(0, min(MAX_K, k))
    if k == 0:
        return []

    prompt = _PROMPT_TMPL.format(slice_text=slice_text, k=k)
    raw_text = await _llm.generate_json(
        prompt, max_tokens=1024, purpose="maestro_long", user_id=user_id,
    )

    # The LLM is asked for a JSON ARRAY. Defensive parsing: pull the
    # first JSON array out of the response if there's accidental prose.
    parsed: Optional[list] = None
    try:
        parsed_any = json.loads(raw_text)
        if isinstance(parsed_any, list):
            parsed = parsed_any
    except json.JSONDecodeError:
        parsed = None
    if parsed is None:
        # Try to locate `[` … matching `]` in the response.
        l = raw_text.find("[")
        r = raw_text.rfind("]")
        if 0 <= l < r:
            try:
                parsed_any = json.loads(raw_text[l : r + 1])
                if isinstance(parsed_any, list):
                    parsed = parsed_any
            except json.JSONDecodeError:
                parsed = None
    if not parsed:
        return []

    coerced: list[Candidate] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        c = _coerce_candidate(raw)
        if c is not None:
            coerced.append(c)

    if not coerced:
        return []

    deduped = await _dedup(coerced)
    return deduped[:k]
