"""Hermetic tests for the tuning sidecar's real eval scorer.

No live LLM (replay + judge are monkeypatched at the scorer's seams) and no
live skillforge (the repo-wide autouse fixture keeps the adapter DEFAULT-OFF).
The menu render / guide render paths run for real — they are pure/local.
"""
import asyncio

import pytest

from services.tuning import scorer


@pytest.fixture(autouse=True)
def _clear_cache():
    scorer._cache.clear()
    yield
    scorer._cache.clear()


def _scenario(**over):
    sc = {
        "input": "plot the parabola y = x^2 over [-3, 3]",
        "transcript": "A parabola bottoms out at the origin.",
        "expect_guide": "plot",
    }
    sc.update(over)
    return sc


class _Replay:
    """Stands in for scorer._replay; records calls, returns canned capture."""

    def __init__(self, selected=(), authored=()):
        self.calls = []
        self._selected = set(selected)
        self._authored = list(authored)

    async def __call__(self, menu_config, scenario):
        self.calls.append((menu_config, scenario))
        return set(self._selected), list(self._authored)


class _Judge:
    def __init__(self, score=0.8):
        self.calls = []
        self._score = score

    async def __call__(self, menu_text, scenario, selected, authored):
        self.calls.append((menu_text, scenario, selected, authored))
        return self._score


def test_right_pick_scores_judge(monkeypatch):
    replay = _Replay(selected={"plot"}, authored=["```plot\n{}\n```"])
    judge = _Judge(score=0.8)
    monkeypatch.setattr(scorer, "_replay", replay)
    monkeypatch.setattr(scorer, "_judge", judge)

    out = asyncio.run(scorer.score(body="Pick a guide:", config={}, scenario=_scenario()))
    assert out == {"ok": True, "quality": 0.8, "outcome": 0.8}
    assert len(replay.calls) == 1 and len(judge.calls) == 1
    # the judge saw the rendered menu with the candidate lead-in folded in
    assert judge.calls[0][0].startswith("Pick a guide:")
    assert judge.calls[0][3] == {"plot"}  # authored modalities, fence-parsed


def test_wrong_pick_fails_without_judge(monkeypatch):
    judge = _Judge()
    monkeypatch.setattr(scorer, "_replay", _Replay(selected={"mermaid"}))
    monkeypatch.setattr(scorer, "_judge", judge)

    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert out == {"ok": False, "quality": 0.0, "outcome": 0.0}
    assert judge.calls == []  # quality never gates alone; no ok → no judge


def test_no_pick_fails(monkeypatch):
    monkeypatch.setattr(scorer, "_replay", _Replay(selected=set()))
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert out == {"ok": False, "quality": 0.0, "outcome": 0.0}


def test_expect_guide_off_menu_skips_replay(monkeypatch):
    replay = _Replay(selected={"plot"})
    monkeypatch.setattr(scorer, "_replay", replay)

    out = asyncio.run(scorer.score(
        body="", config={"offer": ["mermaid"]}, scenario=_scenario(expect_guide="plot"),
    ))
    assert out == {"ok": False, "quality": 0.0, "outcome": 0.0}
    assert replay.calls == []  # necessary condition failed — no LLM spend


def test_replay_exception_is_fail_safe(monkeypatch):
    async def _boom(menu_config, scenario):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(scorer, "_replay", _boom)
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert out == {"ok": False, "quality": 0.0, "outcome": 0.0}


def test_result_cache_scores_once(monkeypatch):
    replay = _Replay(selected={"plot"}, authored=["```plot\n{}\n```"])
    monkeypatch.setattr(scorer, "_replay", replay)
    monkeypatch.setattr(scorer, "_judge", _Judge(score=0.5))

    first = asyncio.run(scorer.score(body="B", config={}, scenario=_scenario()))
    second = asyncio.run(scorer.score(body="B", config={}, scenario=_scenario()))
    assert first == second and len(replay.calls) == 1  # cache hit

    asyncio.run(scorer.score(body="B2", config={}, scenario=_scenario()))
    assert len(replay.calls) == 2  # different candidate → fresh replay


def test_body_overrides_config_select_prompt(monkeypatch):
    replay = _Replay(selected={"plot"}, authored=[])
    monkeypatch.setattr(scorer, "_replay", replay)
    monkeypatch.setattr(scorer, "_judge", _Judge())

    asyncio.run(scorer.score(
        body="OVERRIDE", config={"select_prompt": "CFG"}, scenario=_scenario(),
    ))
    assert replay.calls[0][0]["select_prompt"] == "OVERRIDE"

    asyncio.run(scorer.score(
        body="", config={"select_prompt": "CFG"}, scenario=_scenario(input="x2"),
    ))
    assert replay.calls[1][0]["select_prompt"] == "CFG"


def test_parse_score():
    assert scorer._parse_score('{"score": 0.7, "reason": "ok"}') == 0.7
    assert scorer._parse_score('noise {"score": 1.4} noise') == 1.0  # clamped
    assert scorer._parse_score("score: 0.25 because") == 0.25  # bare-float fallback
    assert scorer._parse_score("no numbers here") == 0.0  # unparseable fails LOW
    assert scorer._parse_score("") == 0.0


def test_stubbed_writer_tools_surface():
    tools = {t.name: t for t in scorer._stubbed_writer_tools()}
    assert set(tools) == {"load_guide", "mount_template", "edit_note"}
    # authoring verbs are inert recorders...
    assert "stub" in asyncio.run(tools["mount_template"].executor({}))
    assert "stub" in asyncio.run(tools["edit_note"].executor({"ops": []}))
    # ...while load_guide still renders the real guide body
    assert "=== GUIDE: plot ===" in asyncio.run(tools["load_guide"].executor({"ids": ["plot"]}))
