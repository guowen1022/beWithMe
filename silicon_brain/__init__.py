"""silicon_brain — the user's neutral, third-party-summarized data.

Holds only what the user is *intrinsically*: their account, what they wrote
about themselves, what they uploaded, what they say they prefer. Anything a
persona authors *about* the user (e.g., teacher's mastery model, session
notes, recommendations) lives in `persona/<name>/`, not here.

Public API: import the common surface from here, or reach into submodules.
"""
from silicon_brain.models.user import User
from silicon_brain.models.profile import Profile
from silicon_brain.models.document import Document, DocumentChunk
from silicon_brain.models.user_preferences import UserPreferences

__all__ = [
    "User",
    "Profile",
    "Document",
    "DocumentChunk",
    "UserPreferences",
]
