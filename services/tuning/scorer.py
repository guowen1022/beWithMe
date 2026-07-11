"""Real eval signal for `skill_menu.canvas_guides` — the scorer behind POST /eval.

skillforge RPCs this sidecar once per (candidate, scenario); the score comes
from REPLAYING the production canvas-writer over the candidate menu:

  1. render the menu via the exact serving path (`render_root_menu`) with the
     candidate body folded in as `select_prompt` (body wins over config,
     mirroring the proxy this replaces);
  2. run the real writer tool loop — same prompt builder, same writer-lane
     tools, same profile. `load_guide` executes for real (pure); the authoring
     verbs `mount_template`/`edit_note` are swapped for no-op recorders so a
     replay never touches a canvas, a note cache, or the DB;
  3. ``ok`` = the writer opened the scenario's expected guide AND that guide
     renders a real body. Deterministic-first — the hack-proof necessary
     condition; the judge below never gates alone (anti-Goodhart);
  4. ``quality`` = LLM-judge 0–1: how well did THIS menu steer the writer for
     THIS request. Forced to 0.0 whenever ``ok`` is false. ``outcome`` mirrors
     ``quality`` (the offline analog of telemetry's outcome_scalar).

Fail-safe: any internal error (LLM down, bad scenario, timeout) returns
``{ok: False, quality: 0.0, outcome: 0.0}`` — skillforge treats that as
fail-CLOSED, so a broken signal can never promote a candidate.

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
from dataclasses import replace
from typing import Dict, List, Set, Tuple
from uuid import UUID

from infra.model import llm
from persona.teacher.prompts import canvas_guides
from persona.teacher.prompts.canvas_guides import _offered_ids
from persona.teacher.prompts.canvas_writer import build as build_canvas_writer_prompt
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from persona.teacher.tools.manifest import build_tools


# Synthetic principal for replays — never a real user; tool executors that
# would need one are stubbed out below anyway.
_EVAL_USER_ID = UUID("00000000-0000-4000-8000-0000e5a10001")

# Hard ceiling on one scenario's replay+judge. skillforge's RemoteEvalBackend
# has its own client-side timeout (SKILLFORGE_REMOTE_EVAL_TIMEOUT, dev: 180s);
# this server-side guard just guarantees a hung LLM call resolves to the
# fail-safe zeros instead of an open socket.
_REPLAY_TIMEOUT_S = 150.0

_FAIL: Dict[str, object] = {"ok": False, "quality": 0.0, "outcome": 0.0}

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


async def _stub_mount(args: dict) -> str:
    return "mounted (eval replay stub — nothing written)"


async def _stub_edit(args: dict) -> str:
    return "edited (eval replay stub — nothing written)"


_AUTHORING_STUBS = {"mount_template": _stub_mount, "edit_note": _stub_edit}


def _stubbed_writer_tools():
    """The production writer-lane toolset (load_guide + mount_template +
    edit_note) with the authoring executors replaced by no-op recorders.
    The LLM-facing surface (names, descriptions, schemas — including any
    served tool.* description tuning) is byte-identical to production;
    only the side effects are gone. Authored markdown is captured from the
    loop's tool_call events, not executor returns, so nothing is lost."""
    out = []
    for t in build_tools(_EVAL_USER_ID, lane="writer"):
        stub = _AUTHORING_STUBS.get(t.name)
        out.append(replace(t, executor=stub) if stub else t)
    return out


async def _replay(menu_config: dict, scenario: dict) -> Tuple[Set[str], List[str]]:
    """Run the real canvas-writer loop over the candidate menu. Returns
    (guides the writer opened, markdown it authored) — the same capture the
    production telemetry path does in persona/teacher/writer.py."""
    parts = build_canvas_writer_prompt(
        question=str(scenario.get("input") or ""),
        voice_transcript=str(scenario.get("transcript") or ""),
        canvas_state=None,
        existing_notes=None,
        related_notes=None,
        menu_config=menu_config,
    )
    selected: Set[str] = set()
    authored_parts: List[str] = []
    async for evt in run_teacher_tool_loop(
        static_system=parts.static_system,
        static_user_passage=parts.static_user_passage,
        dynamic_user=parts.dynamic_user,
        prior_messages=None,
        tools=_stubbed_writer_tools(),
        purpose="skillforge-eval",
        user_id=_EVAL_USER_ID,
        max_tokens=8192,
        max_iterations=canvas_guides.MAX_GUIDE_DEPTH + 1,
        terminal_tools={"mount_template", "edit_note"},
        profile="voice",
    ):
        if evt.get("kind") != "tool_call":
            continue
        name = evt.get("name")
        args = evt.get("arguments") or {}
        if name == "load_guide":
            ids = args.get("ids") or []
            if isinstance(ids, list):
                selected.update(str(i).strip() for i in ids)
        elif name == "mount_template":
            md = (args.get("params") or {}).get("markdown")
            if isinstance(md, str):
                authored_parts.append(md)
        elif name == "edit_note":
            for op in args.get("ops") or []:
                if isinstance(op, dict) and isinstance(op.get("md"), str):
                    authored_parts.append(op["md"])
    return selected, authored_parts


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


async def _score_uncached(body: str, config: dict, scenario: dict) -> Dict[str, object]:
    expect = str(scenario.get("expect_guide") or "").strip()
    menu_config = dict(config or {})
    if isinstance(body, str) and body.strip():
        menu_config["select_prompt"] = body

    # Necessary condition, no LLM spend: the right guide must be on the menu.
    if not expect or expect not in _offered_ids(menu_config):
        return dict(_FAIL)

    selected, authored_parts = await _replay(menu_config, scenario)

    # "Render the picked guide": the expected pick must resolve to a real
    # guide body, not the graceful-degradation note.
    render_ok = "=== GUIDE:" in canvas_guides.get_guide([expect])
    if expect not in selected or not render_ok:
        return dict(_FAIL)

    authored = canvas_guides.authored_modalities("\n".join(authored_parts))
    quality = await _judge(
        canvas_guides.render_root_menu(menu_config), scenario, selected, authored
    )
    return {"ok": True, "quality": quality, "outcome": quality}


async def score(*, body: str, config: dict, scenario: dict) -> Dict[str, object]:
    """One scenario → {ok, quality, outcome}. Never raises."""
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
    except Exception:
        traceback.print_exc()
        return dict(_FAIL)


__all__ = ["score"]
