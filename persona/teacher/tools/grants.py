"""The teacher persona's tool-selection capability grant.

The teacher may select tools from its own `teacher` domain plus the shared
`common` and `canvas` domains — never `app` or `engineer` tools. This is the
single declarative source for "what may the teacher LLM call." See
ARCHITECTURE.md §4.4 and `infra/model/authz.py`.
"""
from __future__ import annotations

from infra.model.authz import CapabilityGrant
from infra.model.tools import ToolDomain

TEACHER_GRANT = CapabilityGrant(
    persona="teacher",
    domains=frozenset({ToolDomain.TEACHER, ToolDomain.COMMON, ToolDomain.CANVAS}),
)

__all__ = ["TEACHER_GRANT"]
