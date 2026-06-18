"""The app_operator persona's tool-selection capability grant.

Deliberately tiny: `app`-domain verbs only (switch_user, go_home, show_mirror).
The single declarative source for "what may the app_operator LLM call." See
ARCHITECTURE.md §4.4 and `infra/model/authz.py`.
"""
from __future__ import annotations

from infra.model.authz import CapabilityGrant
from infra.model.tools import ToolDomain

APP_OPERATOR_GRANT = CapabilityGrant(
    persona="app_operator",
    domains=frozenset({ToolDomain.APP}),
)

__all__ = ["APP_OPERATOR_GRANT"]
