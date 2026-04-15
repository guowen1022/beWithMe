"""Silicon Brain — the user's auto-profile.

A complete digital representation of the user, readable by any agent:
  - How the user thinks (preferences, style)
  - What the user knows (concepts, mastery, graph)
  - What the user has done (interactions, history)

Sub-modules:
  user_profile/ — static preferences, preference embedding, session signals
  knowledge/    — concepts, edges, HLR mastery, graph walks
"""

from app.silicon_brain.state import get_brain_state, BrainState

__all__ = ["get_brain_state", "BrainState"]
