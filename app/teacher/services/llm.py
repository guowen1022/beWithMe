"""Re-export from shared LLM service.

llm.py is shared infrastructure (used by both teacher and silicon_brain).
The canonical location is app/services/llm.py. Teacher re-exports for
convenience so teacher-internal code can import from its own services/.
"""
from app.services.llm import *  # noqa: F401,F403
