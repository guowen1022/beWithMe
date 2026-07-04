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


# --- collect_result (telemetry composition) ---------------------------------

def test_collect_result_noop_when_disabled(monkeypatch):
    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf.collect_result("tool.x", ok=True)
    assert fired == []


def test_collect_result_composes_telemetry_event(monkeypatch):
    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {"tool.x": {"enabled": True, "version": "v3"}})
    sf.collect_result("tool.x", ok=True, latency_ms=1200, outcome_scalar=1.0)
    (event,) = fired
    assert event["tunable_id"] == "tool.x"
    assert event["host"] == "beWithMe"
    assert event["variant_version"] == "v3"          # from the active snapshot
    assert event["result"] == {"ok": True, "latency_ms": 1200}
    assert event["outcome_scalar"] == 1.0
    assert event["correlation_id"]


def test_collect_result_honors_explicit_variant_version(monkeypatch):
    # The version the execution actually ran under must win over a re-resolve
    # here (which drifts if a background snapshot refresh lands mid-render).
    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {"tool.x": {"enabled": True, "version": "v9"}})
    sf.collect_result("tool.x", ok=True, variant_version="v-that-ran")
    assert fired[-1]["variant_version"] == "v-that-ran"   # not the snapshot's v9


# --- present_coordinate_grid: description/config injection + telemetry ------

_PCG_ID = "tool.present_coordinate_grid"


def test_pcg_description_injected_and_bounded():
    from workshop.canvas.tools import present_coordinate_grid as pcg
    # baseline when off
    assert pcg.build_spec(uuid.uuid4()).description == pcg._DESCRIPTION
    # injected variant wins
    sf._set_for_test("http://edge", {_PCG_ID: {"config": {"description": "tuned!"}}})
    assert pcg.build_spec(uuid.uuid4()).description == "tuned!"
    # out-of-bounds variants fall back
    sf._set_for_test("http://edge", {_PCG_ID: {"config": {"description": "x" * 3000}}})
    assert pcg.build_spec(uuid.uuid4()).description == pcg._DESCRIPTION


def test_pcg_telemetry_and_max_duration(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from workshop.canvas.tools import present_coordinate_grid as pcg
    from workshop.canvas.tools import _manim_scene

    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {_PCG_ID: {"config": {"max_duration": 5}}})

    # bad spec → ok=False telemetry, error result
    result = asyncio.run(pcg.present_coordinate_grid(
        user_id=uuid.uuid4(), args={"functions": []}))
    assert "error" in result
    assert fired[-1]["result"]["ok"] is False

    # success path (render + mount stubbed) → duration capped, ok=True
    seen = {}
    real_generate = _manim_scene.generate_scene

    def spy_generate(spec):
        seen["duration"] = spec["duration"]
        return real_generate(spec)

    async def fake_render(source, out_path, **kw):
        return 0.4

    async def fake_mount(**kw):
        return SimpleNamespace(block_id="tick-check", template="note", deleted=[])

    monkeypatch.setattr(pcg._manim_scene, "generate_scene", spy_generate)
    monkeypatch.setattr(pcg._manim_scene, "render_scene", fake_render)
    monkeypatch.setattr(pcg, "mount_template", fake_mount)
    result = asyncio.run(pcg.present_coordinate_grid(
        user_id=uuid.uuid4(),
        args={"title": "T", "functions": [{"expression": "x*x"}], "duration": 18},
    ))
    assert result["block_id"] == "tick-check"
    assert seen["duration"] == 5.0                   # tuned cap applied
    assert fired[-1]["result"]["ok"] is True
    assert fired[-1]["result"]["latency_ms"] == 400


def test_pcg_no_success_telemetry_when_mount_fails(monkeypatch):
    # Render succeeds but the mount raises (e.g. slug collision): the tool
    # returns an error to the LLM, so it must NOT also bank a skillforge win
    # — otherwise promote/rollback trains on a phantom success.
    import asyncio
    from workshop.canvas.tools import present_coordinate_grid as pcg

    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {_PCG_ID: {"enabled": True, "version": "v1"}})

    async def fake_render(source, out_path, **kw):
        return 0.4

    async def boom_mount(**kw):
        raise ValueError("slug collision")

    monkeypatch.setattr(pcg._manim_scene, "render_scene", fake_render)
    monkeypatch.setattr(pcg, "mount_template", boom_mount)
    result = asyncio.run(pcg.present_coordinate_grid(
        user_id=uuid.uuid4(),
        args={"title": "T", "functions": [{"expression": "x*x"}]},
    ))
    assert "error" in result
    assert not any(e["result"]["ok"] for e in fired)   # no phantom win recorded
