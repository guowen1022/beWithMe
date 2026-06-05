"""Regression guard for the manifest-refactor (2026-05).

Background: persona/teacher/tools/manifest.py was 2180 lines / ~32k tokens
of inline ToolSpec descriptions + executor factories. The refactor co-
located each tool's `build_spec(user_id)` next to its implementation
(under `tools/*.py` and `workshop/canvas/tools/*.py`) so future personas
can reuse the same verbs without copy-paste. The teacher manifest now
just assembles + lane-filters.

The wire form sent to the LLM provider MUST stay byte-identical across
this refactor — the LLM provider's prompt cache is keyed on it, and any
drift means a cache miss + more tokens. This test pins:

    - per-lane tool counts and order
    - sha256 of the answer-lane JSON wire form
    - the shape of each per-tool `build_spec(user_id)` API

Plus a quick sanity that `SiliconBrainClient` is reachable from its new
home in `infra/` (Phase 0 of the same refactor).

Self-contained — no services or DB required. Lives in tests/e2e/ alongside
the live SSE tests because it's testing the same surface they're driving
through.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from uuid import UUID

import pytest

from persona.teacher.tools.manifest import build_tools


# Fixed UUID used for the deterministic build (instead of uuid4() so the
# hash below is reproducible). The build_tools layer must not let user_id
# leak into the wire form — that's part of what this test pins.
PINNED_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


# Golden — captured pre-refactor (see plan in
# /Users/weng/.claude/plans/can-we-have-it-wild-lollipop.md). If a future
# change deliberately edits a tool description or schema, regenerate the
# golden by running:
#
#   .venv/bin/python -c "from tests.e2e.test_manifest_refactor import \
#       _compute_golden; print(_compute_golden())"
#
# and update GOLDEN_ANSWER_LANE_SHA256 + ANSWER_LANE_JSON_BYTES.
GOLDEN_ANSWER_LANE_SHA256 = "21c99e9dca7d94423f4e4e6f8fbdb36d7e879442ea92211a7b09d44de5d94615"
ANSWER_LANE_JSON_BYTES = 44951


# Per-lane counts pinned. These doubled as the verification spec in the
# refactor plan. Updated for PR-2 (Maestro stream + domain READ tools):
# +6 on answer/background/research, +4 on user_facing (stream_emit AND
# stream_query excluded from user_facing — Lane A's single iteration goes
# to speak, not WRITE or heavier reads).
EXPECTED_LANE_COUNTS = {
    "answer": 25,
    "user_facing": 14,
    "background": 23,
    "research": 25,
    "writer": 2,
}


# Historical order — the exact sequence the original hand-authored
# manifest produced, with PR-2's stream + domain READ tools appended
# before start_research. Order matters because the LLM provider's
# cache prefix is sensitive to it.
EXPECTED_ANSWER_LANE_ORDER = [
    "read_media",
    "read_document",
    "search_notes",
    "look_at_image",
    "look_at_video",
    "read_url",
    "browser_set",
    "web_view",
    "list_media",
    "mount_template",
    "edit_note",
    "request_new_block",
    "interactive_graph",
    "push_block_content",
    "point_arrow",
    "speak",
    "layout_blocks",
    "block_action",
    # PR-2 — stream + domain READ tools.
    "stream_emit",
    "stream_query",
    "stream_projection",
    "read_concept_mastery",
    "read_world_knowledge",
    "read_captures",
    "start_research",
]


# (module path, expected ToolSpec.name) for each tool that owns its
# build_spec next to its implementation. Research-lane tools (start_research,
# research_plan, research_note) stay in manifest.py and are not in this list.
TOOLS_WITH_BUILD_SPEC = [
    ("workshop.canvas.tools.read_media",         "read_media"),
    ("workshop.canvas.tools.list_media",         "list_media"),
    ("workshop.canvas.tools.mount_template",     "mount_template"),
    ("workshop.canvas.tools.edit_note",          "edit_note"),
    ("workshop.canvas.tools.request_ui_block",   "request_new_block"),
    ("workshop.canvas.tools.push_block_content", "push_block_content"),
    ("workshop.canvas.tools.interactive_graph",  "interactive_graph"),
    ("workshop.canvas.tools.point_arrow",        "point_arrow"),
    ("workshop.canvas.tools.layout_blocks",      "layout_blocks"),
    ("workshop.canvas.tools.block_action",       "block_action"),
    ("tools.read_document",                      "read_document"),
    ("tools.search_notes",                       "search_notes"),
    ("tools.look_at_image",                      "look_at_image"),
    ("tools.look_at_video",                      "look_at_video"),
    ("tools.read_url",                           "read_url"),
    ("tools.browser_set",                        "browser_set"),
    ("tools.web_view",                           "web_view"),
    ("tools.speak",                              "speak"),
    # PR-2 — stream + domain READ tools.
    ("tools.stream_emit",                        "stream_emit"),
    ("tools.stream_query",                       "stream_query"),
    ("tools.stream_projection",                  "stream_projection"),
    ("tools.read_concept_mastery",               "read_concept_mastery"),
    ("tools.read_world_knowledge",               "read_world_knowledge"),
    ("tools.read_captures",                      "read_captures"),
]


def _answer_lane_wire_blob() -> bytes:
    ts = build_tools(PINNED_USER_ID, "answer")
    return json.dumps(
        [t.to_openai() for t in ts], sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def _compute_golden() -> str:
    """Helper for the maintainer comment above. Not called from any test."""
    blob = _answer_lane_wire_blob()
    return (
        f"bytes={len(blob)} "
        f"sha256={hashlib.sha256(blob).hexdigest()}"
    )


def test_lane_counts_match_baseline():
    for lane, expected in EXPECTED_LANE_COUNTS.items():
        got = build_tools(PINNED_USER_ID, lane)
        assert len(got) == expected, (
            f"{lane!r} lane size drifted: expected {expected}, got {len(got)} "
            f"({[t.name for t in got]})"
        )


def test_answer_lane_order_is_stable():
    names = [t.name for t in build_tools(PINNED_USER_ID, "answer")]
    assert names == EXPECTED_ANSWER_LANE_ORDER, (
        "tool order shifted — the LLM provider's prompt cache will miss. "
        f"got: {names}"
    )


def test_answer_lane_wire_form_is_byte_identical_to_golden():
    blob = _answer_lane_wire_blob()
    assert len(blob) == ANSWER_LANE_JSON_BYTES, (
        f"answer-lane wire size drifted: {len(blob)} != {ANSWER_LANE_JSON_BYTES}"
    )
    sha = hashlib.sha256(blob).hexdigest()
    assert sha == GOLDEN_ANSWER_LANE_SHA256, (
        f"answer-lane wire form drifted from golden.\n"
        f"  got:    {sha}\n"
        f"  golden: {GOLDEN_ANSWER_LANE_SHA256}\n"
        f"If this change is intentional (you edited a tool description or "
        f"schema), update GOLDEN_ANSWER_LANE_SHA256 + ANSWER_LANE_JSON_BYTES "
        f"in this file."
    )


def test_writer_lane_is_only_mount_and_edit():
    names = sorted(t.name for t in build_tools(PINNED_USER_ID, "writer"))
    assert names == ["edit_note", "mount_template"], names


@pytest.mark.parametrize("module_path,expected_name", TOOLS_WITH_BUILD_SPEC)
def test_each_tool_exports_build_spec(module_path: str, expected_name: str):
    """Every refactored tool must expose `build_spec(user_id) -> ToolSpec`
    with an async executor bound by closure. The closure pattern is the
    invariant that prevents the LLM from forging a different user_id."""
    mod = importlib.import_module(module_path)
    assert hasattr(mod, "build_spec"), (
        f"{module_path}.build_spec(user_id) missing after manifest refactor"
    )

    spec = mod.build_spec(PINNED_USER_ID)
    assert spec.name == expected_name, f"{module_path}: name drift {spec.name!r}"
    assert isinstance(spec.description, str) and spec.description
    assert isinstance(spec.params_schema, dict)
    assert spec.params_schema.get("type") == "object"
    assert inspect.iscoroutinefunction(spec.executor), (
        f"{module_path}: executor must be async — got {type(spec.executor)}"
    )


def test_silicon_brain_client_lives_in_infra():
    """Phase-0 regression: SiliconBrainClient moved from persona/teacher/ to
    infra/. Importing from the old path must fail; new path must succeed."""
    from infra.silicon_brain_client import SiliconBrainClient
    assert SiliconBrainClient.__module__ == "infra.silicon_brain_client"

    with pytest.raises(ImportError):
        importlib.import_module("persona.teacher.silicon_brain_client")
