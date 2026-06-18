"""Unit tests for persona tool authorization — the domain-grant model.

Covers the pure authz functions, the dispatch-time guard in the agent loop,
and that each live persona manifest only assembles tools its grant allows.

See ARCHITECTURE.md §4.4 and
architecture-review/proposals/2026-06-17-tool-authorization.md.
"""
import asyncio
import json
from uuid import uuid4

from infra.model.tools import ToolSpec, ToolDomain
from infra.model.authz import CapabilityGrant, authorize, authorized_tools
from infra.model.agent_loop import _execute_tool_calls


TEACHER = CapabilityGrant(
    "teacher", frozenset({ToolDomain.TEACHER, ToolDomain.COMMON, ToolDomain.CANVAS})
)
APP = CapabilityGrant("app_operator", frozenset({ToolDomain.APP}))


def _spec(name: str, domain: ToolDomain = ToolDomain.COMMON) -> ToolSpec:
    async def _noop(args):
        return json.dumps({"ok": True, "tool": name})

    return ToolSpec(
        name=name, description="", params_schema={}, executor=_noop, domain=domain
    )


# --- the pure model --------------------------------------------------------

def test_toolspec_defaults_to_common():
    assert _spec("speak").domain is ToolDomain.COMMON


def test_authorize_in_and_out_of_grant():
    assert authorize(TEACHER, _spec("speak", ToolDomain.COMMON))
    assert authorize(TEACHER, _spec("edit_note", ToolDomain.CANVAS))
    assert authorize(TEACHER, _spec("end_session", ToolDomain.TEACHER))
    assert not authorize(TEACHER, _spec("switch_user", ToolDomain.APP))
    assert not authorize(TEACHER, _spec("replace_page", ToolDomain.ENGINEER))


def test_app_operator_is_app_only():
    assert authorize(APP, _spec("go_home", ToolDomain.APP))
    # app_operator never requested `common`, so even a generic verb is denied.
    assert not authorize(APP, _spec("speak", ToolDomain.COMMON))


def test_authorized_tools_filters():
    specs = [
        _spec("speak", ToolDomain.COMMON),
        _spec("switch_user", ToolDomain.APP),
        _spec("end_session", ToolDomain.TEACHER),
    ]
    assert {s.name for s in authorized_tools(TEACHER, specs)} == {"speak", "end_session"}


# --- the dispatch-time guard ----------------------------------------------

def test_dispatch_rejects_foreign_tool():
    spec = _spec("switch_user", ToolDomain.APP)
    out = asyncio.run(
        _execute_tool_calls(
            [{"name": "switch_user", "arguments": {}}], [spec], 2000, grant=TEACHER
        )
    )
    err = json.loads(out[0]["result"])
    assert "error" in err
    assert "not in persona 'teacher' grant" in err["error"]


def test_dispatch_allows_granted_tool():
    spec = _spec("speak", ToolDomain.COMMON)
    out = asyncio.run(
        _execute_tool_calls(
            [{"name": "speak", "arguments": {}}], [spec], 2000, grant=TEACHER
        )
    )
    assert json.loads(out[0]["result"]).get("ok") is True


def test_dispatch_no_grant_allows_all():
    # Backward compatible: grant=None skips the check entirely.
    spec = _spec("switch_user", ToolDomain.APP)
    out = asyncio.run(
        _execute_tool_calls([{"name": "switch_user", "arguments": {}}], [spec], 2000)
    )
    assert json.loads(out[0]["result"]).get("ok") is True


# --- the live persona manifests -------------------------------------------

def test_teacher_manifest_only_grants_allowed_domains():
    from persona.teacher.tools.manifest import build_tools
    from persona.teacher.tools.grants import TEACHER_GRANT

    for spec in build_tools(uuid4()):
        assert spec.domain in TEACHER_GRANT.domains, (
            f"{spec.name} has domain {spec.domain} outside the teacher grant"
        )


def test_teacher_session_tools_are_teacher_domain():
    from persona.teacher.tools.manifest import build_session_tools

    specs = build_session_tools(uuid4(), uuid4())
    assert {s.name for s in specs} == {"end_session"}
    assert all(s.domain is ToolDomain.TEACHER for s in specs)


def test_app_operator_manifest_is_app_only():
    from persona.app_operator.tools.manifest import build_tools as app_build_tools

    specs = app_build_tools(uuid4())
    assert specs, "app_operator should have tools"
    assert all(s.domain is ToolDomain.APP for s in specs)
    assert {"switch_user", "go_home", "show_mirror"} <= {s.name for s in specs}
