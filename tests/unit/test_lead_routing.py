"""Unit tests for the lead-pass routing tool + produced-materials helpers.

Covers `request_handoff` (the generalized router that replaced
`request_session_control`) and the pure formatters in `_produced_notes` that
feed the teacher its own drawn materials. See the lead/deep two-stage refactor.
"""
import asyncio
import json
from uuid import uuid4

from infra.model.tools import ToolDomain

from persona.teacher.tools import request_handoff
from persona.teacher.contexts import _produced_notes as pn


# --- request_handoff router tool -------------------------------------------

def test_handoff_spec_shape():
    spec = request_handoff.build_spec(uuid4())
    assert spec.name == request_handoff.NAME == "request_handoff"
    assert spec.domain is ToolDomain.TEACHER
    target = spec.params_schema["properties"]["target"]
    assert set(target["enum"]) == {
        request_handoff.TARGET_SESSION,
        request_handoff.TARGET_DEEP,
    }
    assert "target" in spec.params_schema["required"]


def test_handoff_executor_echoes_target():
    spec = request_handoff.build_spec(uuid4())
    out = asyncio.run(spec.executor({"target": "deep"}))
    assert json.loads(out) == {"ok": True, "routed": "deep"}
    out = asyncio.run(spec.executor({"target": "session"}))
    assert json.loads(out)["routed"] == "session"


# --- produced-notes formatters (pure) --------------------------------------

def test_title_from_md_prefers_heading():
    assert pn._title_from_md("# LRU Cache\n\nbody", "lru-cache") == "LRU Cache"


def test_title_from_md_falls_back_to_slug():
    # First non-empty line isn't a heading -> humanize the slug.
    assert pn._title_from_md("**LRU** stands for…", "lru-cache") == "Lru Cache"
    assert pn._title_from_md(None, "big-o-notation") == "Big O Notation"


def test_render_inventory_titles_only_no_contents():
    notes = [
        {"slug": "lru-cache", "title": "LRU Cache", "age_s": 120.0, "md": "secret body"},
        {"slug": "heap", "title": "Heap", "age_s": None, "md": "more"},
    ]
    out = pn.render_inventory(notes)
    assert "NOTES YOU'VE DRAWN" in out
    assert "lru-cache" in out and "LRU Cache" in out
    assert "2m ago" in out and "earlier" in out
    # Inventory must NOT leak note contents — that's the deep pass's job.
    assert "secret body" not in out


def test_render_inventory_empty_is_blank():
    assert pn.render_inventory([]) == ""
    assert pn.render_full([]) == ""


def test_render_full_includes_markdown_and_truncates():
    long_md = "x" * 5000
    notes = [{"slug": "big", "title": "Big", "age_s": 5.0, "md": long_md}]
    out = pn.render_full(notes, max_chars=100)
    assert "NOTE YOU DREW" in out and "slug=big" in out
    assert "…(truncated)" in out
    assert len(out) < 1000


def test_format_age_buckets():
    assert pn._format_age(None) == "earlier"
    assert pn._format_age(30) == "30s ago"
    assert pn._format_age(120) == "2m ago"
    assert pn._format_age(7200) == "2h ago"
