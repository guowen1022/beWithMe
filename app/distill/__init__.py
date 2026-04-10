"""Distill module: learner modeling with half-life regression and preference distillation."""

from app.distill.state import get_learner_state, LearnerState, ConceptSnapshot
from app.distill.concept_tracker import extract_concepts, upsert_concepts_hlr
from app.distill.preference_distiller import (
    distill_preferences,
    get_or_create_preferences,
    should_auto_distill,
)

__all__ = [
    "get_learner_state",
    "LearnerState",
    "ConceptSnapshot",
    "extract_concepts",
    "upsert_concepts_hlr",
    "distill_preferences",
    "get_or_create_preferences",
    "should_auto_distill",
]
