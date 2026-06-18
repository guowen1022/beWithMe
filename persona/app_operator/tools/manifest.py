"""app_operator's tool allowlist.

Today this is exactly the "app actions" set (`persona/app_operator/tools/app_action.py`):
switch_user, go_home, show_mirror. Kept as a manifest (rather than calling
`build_app_action_specs` directly at the call site) so future app-level
config tools slot in here, mirroring `persona/teacher/tools/manifest.py`.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from infra.model.tools import ToolSpec
from infra.model.authz import authorized_tools
from persona.app_operator.tools.app_action import build_app_action_specs
from persona.app_operator.tools.grants import APP_OPERATOR_GRANT


def build_tools(user_id: UUID) -> List[ToolSpec]:
    # Outer fence: app_operator may select only `app`-domain tools (§4.4).
    return authorized_tools(APP_OPERATOR_GRANT, build_app_action_specs(user_id))


__all__ = ["build_tools"]
