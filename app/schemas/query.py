"""Backward-compatible re-exports — schemas now live in their modules."""
from app.teacher.schemas import AskRequest, AskResponse  # noqa: F401
from app.silicon_brain.schemas import (  # noqa: F401
    ProfileRead, ProfileUpdate, InteractionRead, DocumentCreate, DocumentRead,
)
