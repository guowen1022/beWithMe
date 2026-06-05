"""Unit tests for Maestro candidate coercion.

Regression guard for the cache-key mismatch bug: the real LLM was free to
invent a per-candidate `persona_purpose` slug, which got stored on the inbox
proposal and seeded into the Maestro cache under that slug — but the agent
(persona/teacher/contexts/answer.py) and the short instance
(services/maestro/short.py) only ever read the canonical key. Seed-key !=
read-key silently disabled posture honoring. `_coerce_candidate` must force
the canonical purpose regardless of what the model returns.
"""
from services.maestro.candidates import (
    PERSONA_PURPOSE,
    _coerce_candidate,
)


def _raw(**overrides):
    base = {
        "title": "Decode codons from photo",
        "posture": "deepen",
        "action_shape": "inbox-proposal",
        "opening": "Use yesterday's tRNA photo to decode a few codons.",
        "prior": 0.7,
    }
    base.update(overrides)
    return base


def test_persona_purpose_is_forced_to_canonical_when_model_supplies_a_slug():
    c = _coerce_candidate(_raw(persona_purpose="teacher:wobble-clarify"))
    assert c is not None
    assert c.persona_purpose == PERSONA_PURPOSE


def test_persona_purpose_is_canonical_when_model_omits_it():
    # The prompt no longer asks for persona_purpose; a candidate without it
    # must still coerce successfully (and carry the canonical key).
    c = _coerce_candidate(_raw())
    assert c is not None
    assert c.persona_purpose == PERSONA_PURPOSE


def test_to_dict_carries_canonical_purpose():
    c = _coerce_candidate(_raw(persona_purpose="teacher:anything"))
    assert c.to_dict()["persona_purpose"] == PERSONA_PURPOSE


def test_invalid_posture_still_rejected():
    assert _coerce_candidate(_raw(posture="vibing")) is None


def test_invalid_action_shape_still_rejected():
    assert _coerce_candidate(_raw(action_shape="telepathy")) is None


def test_missing_title_or_opening_rejected():
    assert _coerce_candidate(_raw(title="")) is None
    assert _coerce_candidate(_raw(opening="   ")) is None


def test_prior_clamped_to_unit_interval():
    assert _coerce_candidate(_raw(prior=5.0)).prior == 1.0
    assert _coerce_candidate(_raw(prior=-2.0)).prior == 0.0
