"""silicon_brain — the user's auto-profile + knowledge graph layer.

Public API: import from this package directly for the common surface, or
reach into submodules for less-used internals (brain_builder, retrieval, etc.).
"""
from silicon_brain.state import BrainState, get_brain_state
from silicon_brain.models.user import User
from silicon_brain.models.profile import Profile
from silicon_brain.models.document import Document, DocumentChunk
from silicon_brain.models.interaction import Interaction
from silicon_brain.knowledge.models import ConceptNode

__all__ = [
    "BrainState",
    "get_brain_state",
    "User",
    "Profile",
    "Document",
    "DocumentChunk",
    "Interaction",
    "ConceptNode",
]
