"""Unit tests for the skillforge tuning adapter + the teacher build_tools gate.

The adapter is DEFAULT OFF (empty edge_url): resolve() returns a baseline (enabled)
and collect() is a no-op, so beWithMe is unchanged. When enabled with a snapshot,
a tool can be disabled via `tool.<name>`.

See infra/skillforge_client.py and brainstorm/tool-refining/ (docs 05/13).
"""
import uuid

import pytest

from infra import skillforge_client as sf


@pytest.fixture(autouse=True)
def _reset():
    # Keep global adapter state from leaking into other test modules.
    sf._reset_for_test()
    yield
    sf._reset_for_test()


# --- the adapter -----------------------------------------------------------

def test_disabled_by_default_resolves_baseline_enabled():
    r = sf.resolve("tool.speak")
    assert r.enabled is True and r.version == "baseline"
    assert bool(r) is True


def test_collect_is_noop_when_disabled():
    # Must not raise or block when off.
    sf.collect({"correlation_id": "c", "host": "beWithMe", "tunable_id": "t",
                "variant_version": "v1", "result": {"ok": True}})


def test_resolve_reads_injected_snapshot():
    sf._set_for_test("http://edge", {"tool.speak": {"enabled": False, "version": "v2"}})
    r = sf.resolve("tool.speak")
    assert r.enabled is False and r.version == "v2"
    # unknown tunable still fails open (baseline enabled)
    assert sf.resolve("tool.unknown").enabled is True


# --- the build_tools gate --------------------------------------------------

def test_gate_is_noop_when_disabled():
    from persona.teacher.tools.manifest import build_tools
    names = {t.name for t in build_tools(uuid.uuid4())}
    assert "speak" in names  # nothing dropped when skillforge is off


def test_gate_drops_a_disabled_tool_when_enabled():
    from persona.teacher.tools.manifest import build_tools
    sf._set_for_test("http://edge", {"tool.speak": {"enabled": False}})
    names = {t.name for t in build_tools(uuid.uuid4())}
    assert "speak" not in names        # skillforge disabled it
    assert "mount_template" in names    # other tools unaffected
