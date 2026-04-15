"""Brain Builder — builds and maintains the silicon brain.

Takes structured learnings from ANY agent (teacher, helper, calendar, etc.)
and integrates them into the user's silicon brain:
  - Extracts and upserts concepts (HLR)
  - Creates concept edges
  - EMA-updates preference embedding
  - Auto-distills categorical preferences
"""

from app.brain_builder.ingester import AgentLearning, process_learning

__all__ = ["AgentLearning", "process_learning"]
