"""Tier-1 grid-spec eval: the scorer, the scenario set, and /eval dispatch.

Fully hermetic and LLM-free by construction — which is the property under test
as much as anything else. `normalize_spec` is a pure function, so these run in
milliseconds and a failure has exactly one cause.
"""
import pytest
from fastapi.testclient import TestClient

from persona.teacher.prompts.canvas_guides import MENU_TUNABLE_ID
from services.tuning import main as tuning_main
from services.tuning import scorer_grid
from services.tuning.scenarios_grid import GRID_TUNABLE_ID, SCENARIOS
from workshop.canvas.tools import _manim_scene
from workshop.canvas.tools.present_coordinate_grid import _TUNABLE_ID

_LABEL_FIELDS = ("region", "split")


async def _score(scenario, config=None):
    return await scorer_grid.score(body="", config=config or {}, scenario=scenario)


# ------------------------------------------------------------ the scenario set

def test_tunable_id_matches_what_the_tool_resolves():
    # If these drift, the served config and the eval signal describe different
    # things — the loop would tune a tunable nothing reads.
    assert GRID_TUNABLE_ID == _TUNABLE_ID


def test_every_scenario_is_labelled_and_well_formed():
    for sc in SCENARIOS:
        assert sc["expect"] in ("accept", "reject"), sc["input"]
        assert isinstance(sc["spec_args"], dict)
        for f in _LABEL_FIELDS:
            assert sc.get(f), f"{sc['input']}: missing {f}"
        assert sc["split"] in ("train", "holdout")
        if sc["expect"] == "reject":
            # a reject scenario that doesn't pin WHY passes even when the
            # validator starts rejecting for an unrelated reason
            assert sc.get("expect_reason"), sc["input"]


def test_both_sides_of_the_split_cover_every_region():
    train = {sc["region"] for sc in SCENARIOS if sc["split"] == "train"}
    holdout = {sc["region"] for sc in SCENARIOS if sc["split"] == "holdout"}
    assert train == holdout, (
        f"train={sorted(train)} holdout={sorted(holdout)} — a region missing "
        "from holdout can never be caught regressing by the gate; a region "
        "missing from train leaves the proposer unable to diagnose it"
    )


def test_scenario_inputs_are_unique():
    # registration dedups by spec["input"]; a duplicate would silently drop one
    inputs = [sc["input"] for sc in SCENARIOS]
    assert len(inputs) == len(set(inputs))


@pytest.mark.asyncio
async def test_the_baseline_passes_every_scenario():
    """The champion body ships as production's own `_DESCRIPTION`, so the
    baseline must score clean — otherwise the seed set encodes a bug, not a
    contract."""
    for sc in SCENARIOS:
        r = await _score(sc)
        assert r["ok"] is True, f'{sc["input"]}: {r["reason"]}'
        assert r["quality"] == 1.0


# ------------------------------------------------------------------ the scorer

@pytest.mark.asyncio
async def test_accept_case_that_starts_rejecting_is_caught():
    # the validator got stricter / the contract moved
    r = await _score({"input": "x", "spec_args": {"functions": [{"expression": "x^2"}]},
                      "expect": "accept"})
    assert r["ok"] is False and r["quality"] == 0.0
    assert "should have been accepted" in r["reason"]
    assert "x**2" in r["reason"]        # carries the validator's own words


@pytest.mark.asyncio
async def test_reject_case_that_starts_accepting_is_caught():
    r = await _score({"input": "x", "spec_args": {"functions": [{"expression": "x"}]},
                      "expect": "reject", "expect_reason": "whatever"})
    assert r["ok"] is False
    assert "should have been rejected" in r["reason"]


@pytest.mark.asyncio
async def test_rejected_for_the_wrong_reason_is_caught():
    """The subtle one: still rejected, so a bare pass/fail check would call
    this healthy — but the validator is now failing on a different clause."""
    r = await _score({"input": "x",
                      "spec_args": {"functions": [{"expression": "x"}],
                                    "transform": [[1, 0, 0], [0, 1, 0]]},
                      "expect": "reject", "expect_reason": "color"})
    assert r["ok"] is False
    assert "expected 'color'" in r["reason"]
    assert "2x2" in r["reason"]         # names what it actually said


@pytest.mark.asyncio
async def test_accepted_but_structurally_wrong_is_caught():
    # legal spec, wrong answer to the question asked: an "animate the shear"
    # case that emits a bare curve isn't a coordinate-grid animation at all
    r = await _score({"input": "shear", "spec_args": {"functions": [{"expression": "x"}]},
                      "expect": "accept", "expect_fields": ["transform"]})
    assert r["ok"] is False
    assert "missing expected fields" in r["reason"] and "transform" in r["reason"]


@pytest.mark.asyncio
async def test_malformed_scenario_fails_closed():
    for bad in ({}, {"expect": "accept"}, {"spec_args": {}, "expect": "maybe"},
                {"spec_args": "not-a-dict", "expect": "accept"}):
        r = await _score(bad)
        assert r["ok"] is False and r["quality"] == 0.0


@pytest.mark.asyncio
async def test_quality_is_binary():
    """No invented gradient: a well-formedness check has no meaningful middle,
    and faking one would let the gate measure its own noise."""
    seen = set()
    for sc in SCENARIOS:
        seen.add((await _score(sc))["quality"])
    seen.add((await _score({"input": "x", "spec_args": {}, "expect": "accept"}))["quality"])
    assert seen <= {0.0, 1.0}


# --------------------------------------------------------- tuned max_duration

@pytest.mark.asyncio
async def test_tuned_max_duration_tightens_the_spec():
    sc = {"input": "x", "spec_args": {"functions": [{"expression": "x"}], "duration": 20},
          "expect": "accept"}
    r = await _score(sc, config={"max_duration": 6})
    assert r["ok"] is True and "duration=6.0" in r["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    _manim_scene.DURATION_MAX_S + 1,     # above the code-owned ceiling
    _manim_scene.DURATION_MIN_S - 1,     # below the floor
    True,                                # bool is an int subclass
    "8", None,
])
async def test_out_of_range_max_duration_is_ignored_not_clamped(bad):
    """skillforge may TIGHTEN the code-owned budget, never widen it — and a
    candidate proposing an illegal cap must not silently be handed a legal one."""
    sc = {"input": "x", "spec_args": {"functions": [{"expression": "x"}], "duration": 12},
          "expect": "accept"}
    r = await _score(sc, config={"max_duration": bad})
    assert r["ok"] is True and "duration=12.0" in r["reason"]


# --------------------------------------------------------------- /eval dispatch

def _client(monkeypatch):
    monkeypatch.setattr(tuning_main.registration, "register",
                        lambda client=None: {"skipped": True, "reason": "test"})
    return TestClient(tuning_main.app)


def test_eval_routes_grid_scenarios_to_the_grid_scorer(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.post("/eval", json={
            "tunable_id": GRID_TUNABLE_ID, "body": "", "config": {},
            "scenario": SCENARIOS[0],
        }).json()
    assert r["ok"] is True and r["quality"] == 1.0


def test_eval_routes_menu_scenarios_to_the_menu_scorer(monkeypatch):
    seen = {}

    async def _fake(*, body, config, scenario):
        seen["hit"] = True
        return {"ok": True, "quality": 0.5, "outcome": 0.5}

    monkeypatch.setattr(tuning_main.scorer, "score", _fake)
    with _client(monkeypatch) as c:
        r = c.post("/eval", json={
            "tunable_id": MENU_TUNABLE_ID, "body": "b", "config": {}, "scenario": {},
        }).json()
    assert seen.get("hit") and r["quality"] == 0.5


def test_eval_without_tunable_id_falls_back_to_the_menu_scorer(monkeypatch):
    """Back-compat: a skillforge predating the tunable_id payload sends none.
    Such a build could only ever have been asking about the menu."""
    seen = {}

    async def _fake(*, body, config, scenario):
        seen["hit"] = True
        return {"ok": True, "quality": 0.25, "outcome": 0.25}

    monkeypatch.setattr(tuning_main.scorer, "score", _fake)
    with _client(monkeypatch) as c:
        r = c.post("/eval", json={"body": "b", "config": {}, "scenario": {}}).json()
    assert seen.get("hit") and r["quality"] == 0.25


def test_eval_fails_closed_on_an_unknown_tunable(monkeypatch):
    """Never guess a scorer: a wrong-scorer number reads as legitimate and is
    indistinguishable from a real regression."""
    with _client(monkeypatch) as c:
        r = c.post("/eval", json={
            "tunable_id": "tool.does_not_exist", "body": "", "config": {}, "scenario": {},
        }).json()
    assert r == {"ok": False, "quality": 0.0, "outcome": 0.0,
                 "reason": "no scorer registered for tunable 'tool.does_not_exist'"}
