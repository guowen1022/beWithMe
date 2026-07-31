"""Hermetic tests for the tuning sidecar's real eval scorer.

No live LLM (replay + judge are monkeypatched at the scorer's seams) and no
live skillforge (the repo-wide autouse fixture keeps the adapter DEFAULT-OFF).
The menu render / guide render paths run for real — they are pure/local.
"""
import asyncio

import pytest

from persona.teacher.canvas_writer_pass import WriterContractError, WriterPass
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
    """Stands in for scorer._replay; records calls, returns a canned WriterPass."""

    def __init__(self, selected=(), authored=(), failed_because=None):
        self.calls = []
        self._selected = set(selected)
        self._authored = list(authored)
        self._failed_because = failed_because

    async def __call__(self, menu_config, scenario):
        self.calls.append((menu_config, scenario))
        return WriterPass(
            calls={"load_guide": {"ids": sorted(self._selected)}} if self._selected else {},
            trace=[{"kind": "done", "stop_reason": "end_turn"}],
            failed_because=self._failed_because,
            selected_guides=set(self._selected),
            authored_parts=list(self._authored),
        )


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
    assert (out["ok"], out["quality"], out["outcome"]) == (True, 0.8, 0.8)
    # The calls ARE the result — `load_guide(ids)` is what the menu exists to cause —
    # and the trace rides along even on a success.
    assert out["calls"] == {"load_guide": {"ids": ["plot"]}}
    assert out["trace"] and "failed_because" not in out
    assert len(replay.calls) == 1 and len(judge.calls) == 1
    # the judge saw the rendered menu with the candidate lead-in folded in
    assert judge.calls[0][0].startswith("Pick a guide:")
    assert judge.calls[0][3] == {"plot"}  # authored modalities, fence-parsed


def test_wrong_pick_fails_without_judge(monkeypatch):
    judge = _Judge()
    monkeypatch.setattr(scorer, "_replay", _Replay(selected={"mermaid"}))
    monkeypatch.setattr(scorer, "_judge", judge)

    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert (out["ok"], out["quality"]) == (False, 0.0)
    assert out["failed_because"].startswith("wrong_guide:opened=['mermaid']")
    assert judge.calls == []  # quality never gates alone; no ok → no judge


def test_no_pick_is_a_decline_not_a_wrong_answer(monkeypatch):
    """Opening nothing is a real outcome — the writer judged the spoken answer complete on
    its own. Recording it as the same zero as a wrong pick is what made a degenerate eval
    look like a menu regression for a month."""
    monkeypatch.setattr(scorer, "_replay", _Replay(selected=set()))
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert out["ok"] is False
    assert out["failed_because"].startswith("declined:nothing_opened")


def test_expect_guide_off_menu_skips_replay(monkeypatch):
    replay = _Replay(selected={"plot"})
    monkeypatch.setattr(scorer, "_replay", replay)

    out = asyncio.run(scorer.score(
        body="", config={"offer": ["mermaid"]}, scenario=_scenario(expect_guide="plot"),
    ))
    assert out["ok"] is False
    assert out["failed_because"].startswith("not_offered:plot")
    assert replay.calls == []  # necessary condition failed — no LLM spend


def test_missing_ground_truth_is_named_not_scored(monkeypatch):
    replay = _Replay(selected={"plot"})
    monkeypatch.setattr(scorer, "_replay", replay)
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario(expect_guide="")))
    assert out["failed_because"].startswith("no_ground_truth")
    assert replay.calls == []


def test_a_missing_required_input_is_named_rather_than_scored(monkeypatch):
    """THE BUG. The eval used to pass an empty transcript to a writer whose entire job is
    mirroring a spoken answer; it correctly did nothing and the 0.0 was filed as a wrong
    guide. A degenerate run must be a named refusal, not a defensible number."""
    async def _refuse(menu_config, scenario):
        raise WriterContractError("voice_transcript")

    monkeypatch.setattr(scorer, "_replay", _refuse)
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario(transcript="")))
    assert out["ok"] is False
    assert out["failed_because"] == "missing_required_input:voice_transcript"


def test_replay_exception_is_fail_safe_and_says_it_crashed(monkeypatch):
    async def _boom(menu_config, scenario):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(scorer, "_replay", _boom)
    out = asyncio.run(scorer.score(body="", config={}, scenario=_scenario()))
    assert (out["ok"], out["quality"], out["outcome"]) == (False, 0.0, 0.0)
    # A crash and a wrong answer are different events. They used to be the same zeros.
    assert out["failed_because"] == "crashed:RuntimeError: LLM down"


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
