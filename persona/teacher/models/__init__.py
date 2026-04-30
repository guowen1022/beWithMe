"""Teacher's ORM models — teacher's memory of teaching this user.

Importing this package registers every teacher table on infra.db.Base so
SQLAlchemy `create_all` sees them.
"""
from persona.teacher.models.interaction import Interaction
from persona.teacher.models.learning_goal import LearningGoal
from persona.teacher.models.recommendation import Recommendation
from persona.teacher.models.learning_session import LearningSession
from persona.teacher.models.teacher_preference_model import TeacherPreferenceModel
# Importing persona.teacher.knowledge.models registers ConceptNode/Edge on the same Base.
from persona.teacher.knowledge.models import ConceptNode, ConceptEdge

__all__ = [
    "Interaction",
    "LearningGoal",
    "Recommendation",
    "LearningSession",
    "TeacherPreferenceModel",
    "ConceptNode",
    "ConceptEdge",
]
