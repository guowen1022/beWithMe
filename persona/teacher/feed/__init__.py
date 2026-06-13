"""Teacher feed producer — the teacher's intra-source card generation.

The teacher reasons about what the learner should study next (mastery,
knowledge graph, learning-style) and publishes ranked cards into the
shared `feed_candidates` store. The Maestro blends these (across personas)
into the unified feed. Per premise 4: the teacher owns *intra-source*
ranking; the Maestro owns *inter-source* blend + saturation.
"""

from persona.teacher.feed.producer import produce_teacher_feed, SOURCE_PERSONA, PURPOSE

__all__ = ["produce_teacher_feed", "SOURCE_PERSONA", "PURPOSE"]
