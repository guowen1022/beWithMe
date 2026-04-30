"""User profile module: static preferences, preference embedding, and session signals."""

from persona.teacher.preferences.state import get_user_profile, boost_query_embedding, UserProfileState
from persona.teacher.preferences.preference_distiller import (
    distill_preferences,
    get_or_create_preferences,
    should_auto_distill,
)

__all__ = [
    "get_user_profile",
    "boost_query_embedding",
    "UserProfileState",
    "distill_preferences",
    "get_or_create_preferences",
    "should_auto_distill",
]
