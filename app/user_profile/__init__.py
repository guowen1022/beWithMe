"""User profile module: static preferences, preference embedding, and session signals."""

from app.user_profile.state import get_user_profile, boost_query_embedding, UserProfileState
from app.user_profile.preference_distiller import (
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
