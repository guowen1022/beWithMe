"""Structural tests for the curated `skill_menu.canvas_guides` eval set.

These guard the anti-overfitting contract beWithMe shares with skillforge:
every scenario declares a `region` (the partition the gate scores separately)
and a `split` ("train" = the proposer may see it, "holdout" = reserved for the
gate). The load-bearing one is `test_both_regions_on_both_sides_of_split` — a
region present only in `train` is a region whose regression the gate can never
catch, which is the exact failure this labeling exists to prevent.

Pure data assertions: no skillforge, no HTTP, no LLM.
"""
import pytest

from services.tuning.scenarios import SCENARIOS

_SPLITS = {"train", "holdout"}


def _in(split):
    return [sc for sc in SCENARIOS if sc["split"] == split]


def _regions(split):
    return {sc["region"] for sc in _in(split)}


# --- every scenario is labeled ---------------------------------------------

@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda sc: sc["input"][:40])
def test_scenario_declares_region_and_split(sc):
    assert sc.get("region"), "missing region"
    assert sc.get("split") in _SPLITS, f"bad split {sc.get('split')!r}"


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda sc: sc["input"][:40])
def test_region_equals_expect_guide(sc):
    # For this tunable the partition key IS the expected guide. Any drift
    # between the two would silently mis-file a scenario at the gate.
    assert sc["region"] == sc["expect_guide"]


# --- the invariant that makes region-aware gating work ----------------------

def test_both_regions_on_both_sides_of_split():
    train, holdout = _regions("train"), _regions("holdout")
    assert train == holdout == {"plot", "mermaid"}, (
        f"train={sorted(train)} holdout={sorted(holdout)} — a region missing "
        "from holdout can never be caught regressing by the gate; a region "
        "missing from train leaves the proposer unable to diagnose it"
    )


def test_guard_scenarios_are_holdout():
    # skillforge forces this server-side; stay consistent at the source.
    assert all(sc["split"] == "holdout" for sc in SCENARIOS if sc.get("guard"))


def test_split_is_roughly_sixty_forty():
    train = len(_in("train"))
    assert train + len(_in("holdout")) == len(SCENARIOS)
    assert 0.5 <= train / len(SCENARIOS) <= 0.75


def test_inputs_are_unique():
    # Registration dedups by spec["input"]; a duplicate would silently drop a
    # scenario (and could land the two halves of a pair on opposite splits).
    inputs = [sc["input"] for sc in SCENARIOS]
    assert len(set(inputs)) == len(inputs)
