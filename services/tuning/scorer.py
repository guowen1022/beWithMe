"""Real eval signal for `skill_menu.canvas_guides` — the scorer behind POST /eval.

skillforge RPCs this sidecar once per (candidate, scenario); the score comes
from REPLAYING the production canvas-writer over the candidate menu:

  1. render the menu via the exact serving path (`render_root_menu`) with the
     candidate body folded in as `select_prompt` (body wins over config,
     mirroring the proxy this replaces);
  2. run the writer through `persona.teacher.canvas_writer_pass.run_writer_pass`
     — literally the function production calls, not a copy of it. The one
     difference is `stub_executors`: `mount_template`/`edit_note` become no-op
     recorders so a replay never touches a canvas, a note cache, or the DB.
     `load_guide` executes for real (it is pure);
  3. ``ok`` = the writer opened the scenario's expected guide AND that guide
     renders a real body. Deterministic-first — the hack-proof necessary
     condition; the judge below never gates alone (anti-Goodhart);
  4. ``quality`` = LLM-judge 0–1: how well did THIS menu steer the writer for
     THIS request. Forced to 0.0 whenever ``ok`` is false. ``outcome`` mirrors
     ``quality`` (the offline analog of telemetry's outcome_scalar).

This file used to hand-copy the loop out of `persona/teacher/writer.py`. Every
mechanical parameter matched; what differed was an INPUT — production passes a
real spoken answer, this passed `""` — and since the writer's job is mirroring a
spoken answer it correctly did nothing, scored 0.0, and was recorded as a wrong
guide for a month. Two copies of one call drift, and the drift is invisible
because nothing declares what must match.

Fail-safe: any internal error (LLM down, bad scenario, timeout) returns
``ok: False`` with zeros — skillforge treats that as fail-CLOSED, so a broken
signal can never promote a candidate. Every such return now carries
``failed_because``, plus the ``calls`` the writer made and a ``trace`` of
everything else, because a wrong answer, a decline and a crash are three
different events that used to arrive as one set of zeros.

Results are cached for the process lifetime keyed on (body, config, scenario):
`harness.gate` re-scores the champion for every candidate in a refine, so the
cache cuts a refine's wall clock nearly in half and keeps the stochastic
replay's champion numbers stable within a run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import traceback
from collections import OrderedDict
from typing import Dict, Optional, Set
from uuid import UUID

from infra.model import llm
from persona.teacher.canvas_writer_pass import (
    WriterContractError, WriterInputs, WriterPass, recovered_args, run_writer_pass,
    writer_tools,
)
from persona.teacher.prompts import canvas_guides
from persona.teacher.prompts.canvas_guides import _offered_ids


# Synthetic principal for replays — never a real user; tool executors that
# would need one are stubbed out below anyway.
_EVAL_USER_ID = UUID("00000000-0000-4000-8000-0000e5a10001")

# Hard ceiling on one scenario's replay+judge. skillforge's RemoteEvalBackend
# has its own client-side timeout (SKILLFORGE_REMOTE_EVAL_TIMEOUT, dev: 180s);
# this server-side guard just guarantees a hung LLM call resolves to the
# fail-safe zeros instead of an open socket.
_REPLAY_TIMEOUT_S = 150.0

_CACHE_MAX = 256
_cache: "OrderedDict[str, Dict[str, object]]" = OrderedDict()


def _cache_key(body: str, config: dict, scenario: dict) -> str:
    blob = json.dumps(
        {"body": body, "config": config, "scenario": scenario},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
    return hit


def _cache_put(key: str, value: Dict[str, object]) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


_TRUNCATED = json.dumps(
    {"error": "tool arguments were truncated mid-stream — retry with shorter content"})


def _stub(verb: str):
    """A no-op child that still answers the way the real one does.

    A stub must drop the SIDE EFFECT, not the contract. The real authoring executors reject a
    call whose arguments arrived truncated, and the loop grants the model a free retry when a
    round was entirely such bails — so a stub that reports success on a truncated call
    silently removes production's retry from the replay, and the run records an authoring call
    that never authored anything. Same shape as the bug this whole file exists to prevent: a
    difference that is not a side effect.
    """
    async def _run(args: dict) -> str:
        _, truncated = recovered_args(args or {})
        if truncated:
            return _TRUNCATED
        return f"{verb} (eval replay stub — nothing written)"
    return _run


_stub_mount = _stub("mounted")
_stub_edit = _stub("edited")


# The ONLY thing evaluation is allowed to change about the writer: the authoring children
# become no-op recorders, so a replay never touches a canvas, a note cache, or the DB.
# Everything else — prompt, model, token limit, iteration cap, tool surface — comes from
# the same function production calls.
_AUTHORING_STUBS = {"mount_template": _stub_mount, "edit_note": _stub_edit}


def _stubbed_writer_tools():
    """The production writer-lane toolset with the authoring executors stubbed.

    The LLM-facing surface (names, descriptions, schemas — including any served `tool.*`
    description tuning) is byte-identical to production; only the side effects are gone.
    Authored markdown is captured from the loop's tool_call events, not executor returns,
    so nothing is lost."""
    return writer_tools(_EVAL_USER_ID, _AUTHORING_STUBS)


async def _replay(menu_config: dict, scenario: dict) -> WriterPass:
    """Run the canvas writer over the candidate menu — the SAME entry point production
    calls, with the child executors stubbed and the candidate menu injected.

    A scenario may also supply `canvas_state` / `existing_notes` / `related_notes`. They
    were hardcoded to `None` here, which made the prompt's "mount, EDIT, or do nothing"
    branch structurally unreachable in evaluation: only the no-existing-note branch was
    ever scored.
    """
    return await run_writer_pass(
        inputs=WriterInputs(
            question=str(scenario.get("input") or ""),
            voice_transcript=str(scenario.get("transcript") or ""),
            canvas_state=scenario.get("canvas_state"),
            existing_notes=scenario.get("existing_notes"),
            related_notes=scenario.get("related_notes"),
            menu_config=menu_config,
        ),
        user_id=_EVAL_USER_ID,
        purpose="skillforge-eval",
        stub_executors=_AUTHORING_STUBS,
    )


def _judge_prompt(menu_text: str, scenario: dict, selected: Set[str],
                  authored: Set[str]) -> str:
    rubric = scenario.get("rubric") or scenario.get("must_include") or []
    rubric_lines = "\n".join(f"  - {r}" for r in rubric) or "  (none)"
    return (
        "You are grading the MENU of visual guides shown to a canvas-writing "
        "assistant. The menu's one job: given the user's request, steer the "
        "writer to open the correct guide before it draws.\n\n"
        f"MENU SHOWN TO THE WRITER:\n{menu_text}\n\n"
        f"USER REQUEST: {scenario.get('input', '')}\n"
        f"SPOKEN ANSWER THE WRITER FOLLOWS: {scenario.get('transcript', '') or '(none)'}\n"
        f"CORRECT GUIDE: {scenario.get('expect_guide', '')}\n"
        f"GUIDES THE WRITER OPENED: {sorted(selected) or '(none)'}\n"
        f"FENCE MODALITIES THE WRITER AUTHORED: {sorted(authored) or '(none)'}\n"
        f"RUBRIC:\n{rubric_lines}\n\n"
        "The writer DID open the correct guide (already verified). Score how "
        "well the MENU itself earned that: is the lead-in a clear instruction, "
        "do the summaries make the correct guide the obvious pick for this "
        "kind of request, is there any wording that could mislead a borderline "
        "request, and did the authored fence match the opened guide? "
        'Return JSON only: {"score": <0.0-1.0>, "reason": "<one sentence>"}'
    )


def _parse_score(raw: str) -> float:
    """Extract a 0–1 score; anything unparseable fails LOW (fail-closed)."""
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            score = float(json.loads(m.group(0)).get("score", 0.0))
            return max(0.0, min(1.0, score))
    except Exception:
        pass
    m = re.search(r"([01](?:\.\d+)?)", raw or "")
    return max(0.0, min(1.0, float(m.group(1)))) if m else 0.0


async def _judge(menu_text: str, scenario: dict, selected: Set[str],
                 authored: Set[str]) -> float:
    raw = await llm.generate_json(
        _judge_prompt(menu_text, scenario, selected, authored),
        max_tokens=256,
    )
    return _parse_score(raw)


def _fail(why: str, run: Optional[WriterPass] = None) -> Dict[str, object]:
    """A zero that says which zero it is.

    A wrong answer, a deliberate decline, an off-menu scenario and a crash are four
    different events. They all used to arrive as the same `{ok: False, quality: 0.0}`,
    which is why `except Exception: return _FAIL` was indistinguishable from a genuine
    regression — the single most expensive ambiguity in this loop's history.
    """
    out: Dict[str, object] = {"ok": False, "quality": 0.0, "outcome": 0.0,
                              "failed_because": why}
    if run is not None:
        out["calls"] = run.calls
        out["trace"] = run.trace
    return out


async def _score_uncached(body: str, config: dict, scenario: dict) -> Dict[str, object]:
    expect = str(scenario.get("expect_guide") or "").strip()
    menu_config = dict(config or {})
    if isinstance(body, str) and body.strip():
        menu_config["select_prompt"] = body

    if not expect:
        return _fail("no_ground_truth: scenario declares no expect_guide")
    # Necessary condition, no LLM spend: the right guide must be on the menu.
    if expect not in _offered_ids(menu_config):
        return _fail(f"not_offered:{expect} — the candidate menu cannot win this case")

    try:
        run = await _replay(menu_config, scenario)
    except WriterContractError as e:
        # Loud, not a quiet zero. skillforge refuses this case before spending anything
        # once the tunable declares the input; this is the same refusal from the host's
        # side, for a scenario that reached us anyway.
        return _fail(f"missing_required_input:{e.name}")
    if run.failed_because:
        return _fail(run.failed_because, run)

    # "Render the picked guide": the expected pick must resolve to a real
    # guide body, not the graceful-degradation note.
    if "=== GUIDE:" not in canvas_guides.get_guide([expect]):
        return _fail(f"guide_render_failed:{expect}", run)
    if expect not in run.selected_guides:
        # Opening nothing is a real outcome — the writer judged the spoken answer
        # complete on its own — and is not the same event as opening the wrong thing.
        if not run.selected_guides:
            return _fail(f"declined:nothing_opened (expected {expect})", run)
        return _fail(
            f"wrong_guide:opened={sorted(run.selected_guides)} expected={expect}", run)

    authored = canvas_guides.authored_modalities("\n".join(run.authored_parts))
    quality = await _judge(
        canvas_guides.render_root_menu(menu_config), scenario, run.selected_guides, authored
    )
    return {"ok": True, "quality": quality, "outcome": quality,
            "calls": run.calls, "trace": run.trace}


async def score(*, body: str, config: dict, scenario: dict) -> Dict[str, object]:
    """One scenario → {ok, quality, outcome, calls, trace, failed_because}. Never raises."""
    try:
        key = _cache_key(body, config or {}, scenario or {})
        hit = _cache_get(key)
        if hit is not None:
            return dict(hit)
        result = await asyncio.wait_for(
            _score_uncached(body or "", config or {}, scenario or {}),
            timeout=_REPLAY_TIMEOUT_S,
        )
        _cache_put(key, result)
        return dict(result)
    except asyncio.TimeoutError:
        # Not cached: a timeout is a fact about this run, not about this candidate.
        return _fail(f"timeout:{_REPLAY_TIMEOUT_S}s")
    except Exception as e:
        traceback.print_exc()
        return _fail(f"crashed:{type(e).__name__}: {e}"[:500])


__all__ = ["score"]
