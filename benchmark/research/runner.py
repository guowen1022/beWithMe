"""Research benchmark runner.

Drives `persona.teacher.triggers._execute_research` against each
scenario in `benchmark.research.scenarios`. Stubs canvas mount/push,
silicon_brain HTTP, and the `speak` TTS executor so we can run
headless without the desktop or shell sidecar — only the browser
sidecar (BASE_PORT+5) and the LLM provider need to be reachable.

Output: a JSON file under benchmark/results/ with the full transcript
(plan, notes, synthesis, tool-call sequence, timing) plus per-scenario
scoring against `expected_procedure_keywords` and
`expected_result_keywords`.

Usage:
    python -m benchmark.research                # all scenarios
    python -m benchmark.research --scenario 2   # single one
    python -m benchmark.research --base-url http://localhost:8005
                                                # browser sidecar URL
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import patch

# Make sure the repo root is on sys.path so `import persona...` works.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# ---------------------------------------------------------------------------
# Stubs for components we don't run during the benchmark.
# ---------------------------------------------------------------------------

async def _stub_canvas_push(*args, **kwargs):
    """Replaces _push_research_state_to_canvas — silent."""
    return None

async def _stub_enqueue(user_id, event):
    return 1

async def _stub_get_profile(self, user_id):  # noqa: ARG001
    return None

async def _stub_get_talk_preference(self, user_id):  # noqa: ARG001
    return {"desktop": "both", "tablet": "both", "phone": "text"}

async def _stub_aclose(self):  # noqa: ARG001
    return None

async def _stub_read_media(user_id):  # noqa: ARG001
    from infra.contracts.ui import PerceptionInventory
    return PerceptionInventory(canvases=[], voices=[])

async def _stub_get_user_profile(db, user_id, session_id=None):  # noqa: ARG001
    return None

async def _stub_get_concepts(db, user_id, limit=30):  # noqa: ARG001
    return []

async def _stub_drain_notices(user_id):  # noqa: ARG001
    return []

async def _stub_async_session():
    """Yield a no-op context manager replacing async_session()."""
    class _NullSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def execute(self, *a, **kw):
            return None
        async def commit(self):
            return None
    return _NullSession()


# ---------------------------------------------------------------------------
# Scoring.
# ---------------------------------------------------------------------------

def _kw_hits(text: str, keyword_sets: List[List[str]]) -> Dict[str, Any]:
    """For each set, return whether ANY of its keywords appears in `text`
    (case-insensitive substring). Returns hit-mask + count + ratio."""
    t = (text or "").lower()
    matches = []
    for kws in keyword_sets:
        hit_kw = next((k for k in kws if k.lower() in t), None)
        matches.append({
            "any_of": kws,
            "matched": hit_kw,
        })
    matched = sum(1 for m in matches if m["matched"])
    total = len(keyword_sets)
    return {
        "matches": matches,
        "matched_sets": matched,
        "total_sets": total,
        "ratio": (matched / total) if total else 1.0,
    }


def _score_scenario(scenario: dict, run: dict) -> dict:
    """Aggregate scoring. Returns a structured verdict."""
    plan_text = ""
    if run["plan_calls"]:
        steps = run["plan_calls"][-1].get("steps") or []
        plan_text = " | ".join(str(s) for s in steps)
    synth_text = ""
    if run["speak_calls"]:
        synth_text = run["speak_calls"][0].get("text") or ""

    proc = _kw_hits(plan_text, scenario["expected_procedure_keywords"])
    res = _kw_hits(synth_text, scenario["expected_result_keywords"])

    activity_ok = (
        run["plan_calls_count"] >= 1
        and run["note_calls_count"] >= 1
        and run["speak_calls_count"] >= 1
        and (run["plan_calls_count"] + run["note_calls_count"] + run["speak_calls_count"])
            >= scenario["expected_min_tool_calls"]
    )

    # Verdict tiers:
    #   PASS    — proc ≥ 75% AND res ≥ 75% AND activity_ok
    #   PARTIAL — proc ≥ 50% AND res ≥ 50% AND activity_ok
    #   FAIL    — anything less, or activity floor not met
    if not activity_ok:
        verdict = "FAIL"
        reason = "activity floor not met (plan/note/speak missing or under min tool calls)"
    elif proc["ratio"] >= 0.75 and res["ratio"] >= 0.75:
        verdict = "PASS"
        reason = "procedure & result both ≥ 75% keyword coverage"
    elif proc["ratio"] >= 0.50 and res["ratio"] >= 0.50:
        verdict = "PARTIAL"
        reason = "procedure & result both ≥ 50% but below 75% on at least one"
    else:
        verdict = "FAIL"
        reason = (
            f"keyword coverage too low (procedure {proc['ratio']:.0%}, "
            f"result {res['ratio']:.0%})"
        )

    return {
        "verdict": verdict,
        "reason": reason,
        "procedure_score": proc,
        "result_score": res,
        "activity_ok": activity_ok,
    }


# ---------------------------------------------------------------------------
# One scenario.
# ---------------------------------------------------------------------------

async def run_one(scenario: dict, base_url: str) -> dict:
    """Drive a single research scenario. Returns a structured record."""
    print(f"\n{'='*72}")
    print(f"SCENARIO {scenario['id']}: {scenario['name']}")
    print(f"  url:  {scenario['url']}")
    print(f"  goal: {scenario['goal']}")
    print(f"{'='*72}")

    # Preload the page in the browser sidecar (uses its global session page).
    print(f"[setup] preloading page in browser sidecar at {base_url} ...")
    req = urllib.request.Request(
        f"{base_url}/api/browser/session",
        data=json.dumps({
            "action": "goto",
            "url": scenario["url"],
            "wait_until": "domcontentloaded",
        }).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        body = urllib.request.urlopen(req, timeout=25).read()
        loaded = json.loads(body)
        print(f"[setup] loaded title={loaded.get('title','?')[:80]!r}")
    except Exception as e:
        print(f"[setup] FAILED: {e}")
        return {
            "scenario": scenario,
            "error": f"preload failed: {e}",
            "verdict": {"verdict": "FAIL", "reason": "preload failed"},
        }

    # ---- Patch the runtime ---------------------------------------------------
    from persona.teacher.tools import manifest as M
    from persona.teacher.contexts import research as ctx_r
    from persona.teacher import research_state as RS
    from persona.teacher.silicon_brain_client import SiliconBrainClient
    from workshop.canvas.tools import read_media as _rm
    from workshop.canvas.tools import mount_template as _mt
    from workshop.canvas.tools import push_block_content as _pb
    from services.persona.routers import dynamic as _dyn
    from persona.teacher import notices as _notices
    from persona.teacher import preferences as _prefs
    from persona.teacher import knowledge as _know

    user_id = uuid.uuid4()

    plan_calls: List[Dict[str, Any]] = []
    note_calls: List[Dict[str, Any]] = []
    speak_calls: List[Dict[str, Any]] = []
    tool_call_log: List[Dict[str, Any]] = []

    # Wrap the recorded tools so we can audit the run.
    original_make_speak = M._make_speak
    original_make_plan = M._make_research_plan
    original_make_note = M._make_research_note

    def fake_make_speak(uid):
        async def exec_(args):
            speak_calls.append(dict(args))
            tool_call_log.append({"name": "speak", "args": args})
            return json.dumps({"ok": True, "delivered_to": ["bench"]})
        return exec_

    def wrap_plan(uid):
        inner = original_make_plan(uid)
        async def exec_(args):
            plan_calls.append(dict(args))
            tool_call_log.append({"name": "research_plan", "args": args})
            return await inner(args)
        return exec_

    def wrap_note(uid):
        inner = original_make_note(uid)
        async def exec_(args):
            note_calls.append(dict(args))
            tool_call_log.append({"name": "research_note", "args": args})
            return await inner(args)
        return exec_

    M._make_speak = fake_make_speak
    M._make_research_plan = wrap_plan
    M._make_research_note = wrap_note

    # Also log every other tool call by wrapping each non-recorded factory.
    # We do this by monkey-patching the factories that produce the executor —
    # cheap and avoids touching the loop.
    factory_names = [
        "_make_browser_set", "_make_web_view", "_make_read_url",
        "_make_look_at_image", "_make_mount_template",
        "_make_push_block_content", "_make_block_action",
        "_make_layout_blocks", "_make_point_arrow", "_make_interactive_graph",
        "_make_read_media", "_make_read_document", "_make_list_media",
    ]
    saved = {}
    for fn_name in factory_names:
        if hasattr(M, fn_name):
            saved[fn_name] = getattr(M, fn_name)
            tool_human_name = fn_name[len("_make_"):]
            def make_wrapper(orig, tname):
                def factory(uid):
                    inner = orig(uid)
                    async def exec_(args):
                        tool_call_log.append({"name": tname, "args": args})
                        return await inner(args)
                    return exec_
                return factory
            setattr(M, fn_name, make_wrapper(saved[fn_name], tool_human_name))

    async def _noop_mount(**kw):
        return type("R", (), {
            "block_id": kw.get("block_id", "x"),
            "template": kw.get("template_name", "x"),
            "deleted": [],
        })()

    async def _noop_push(**kw):
        return 0

    patches = [
        patch.object(M, "_push_research_state_to_canvas", _stub_canvas_push),
        patch.object(SiliconBrainClient, "get_profile", _stub_get_profile),
        patch.object(SiliconBrainClient, "get_talk_preference", _stub_get_talk_preference),
        patch.object(SiliconBrainClient, "aclose", _stub_aclose),
        patch.object(_rm, "read_media", _stub_read_media),
        patch.object(_dyn, "enqueue_for_user", _stub_enqueue),
        patch.object(_mt, "mount_template", _noop_mount),
        patch.object(_pb, "push_block_content", _noop_push),
        patch.object(_notices, "drain", lambda uid: []),
        patch.object(_prefs, "get_user_profile", _stub_get_user_profile),
        patch.object(_know, "get_concepts", _stub_get_concepts),
    ]
    for p in patches:
        p.start()

    record = {
        "scenario": {
            "id": scenario["id"],
            "name": scenario["name"],
            "url": scenario["url"],
            "goal": scenario["goal"],
        },
    }

    try:
        from persona.teacher.triggers import _execute_research

        # Begin state up front (replaces start_research's responsibility
        # since we're calling _execute_research directly).
        RS.begin(user_id, goal=scenario["goal"])

        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                _execute_research(user_id, scenario["goal"]),
                timeout=scenario["deadline_target_s"] + 30.0,  # outer safety
            )
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
        elapsed = time.monotonic() - t0

        # Tally
        record.update({
            "elapsed_s": round(elapsed, 1),
            "timed_out": timed_out,
            "plan_calls_count": len(plan_calls),
            "note_calls_count": len(note_calls),
            "speak_calls_count": len(speak_calls),
            "tool_call_log_count": len(tool_call_log),
            "tool_call_log": tool_call_log,
            "plan_calls": plan_calls,
            "note_calls": note_calls,
            "speak_calls": speak_calls,
        })
        record["verdict"] = _score_scenario(scenario, record)

        # Console summary
        print(f"\n--- run summary (elapsed {elapsed:.1f}s) ---")
        if plan_calls:
            print("PLAN steps:")
            for i, s in enumerate(plan_calls[-1].get("steps") or []):
                print(f"  {i}. {s}")
        else:
            print("PLAN steps: (none)")
        print(f"NOTES recorded: {len(note_calls)}")
        for c in note_calls:
            print(f"  step={c.get('step_index')} finding={(c.get('finding') or '')[:140]!r}")
        if speak_calls:
            print("SYNTHESIS:")
            print(f"  {(speak_calls[0].get('text') or '')[:600]}")
        else:
            print("SYNTHESIS: (none)")
        v = record["verdict"]
        print(f"\nVERDICT: {v['verdict']} — {v['reason']}")
        print(f"  procedure coverage: {v['procedure_score']['matched_sets']}/{v['procedure_score']['total_sets']}")
        for m in v["procedure_score"]["matches"]:
            mark = "✓" if m["matched"] else "✗"
            print(f"    {mark} need any of {m['any_of']} → {m['matched']!r}")
        print(f"  result coverage:    {v['result_score']['matched_sets']}/{v['result_score']['total_sets']}")
        for m in v["result_score"]["matches"]:
            mark = "✓" if m["matched"] else "✗"
            print(f"    {mark} need any of {m['any_of']} → {m['matched']!r}")
    finally:
        # Restore
        M._make_speak = original_make_speak
        M._make_research_plan = original_make_plan
        M._make_research_note = original_make_note
        for fn_name, fn in saved.items():
            setattr(M, fn_name, fn)
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass
        # Drop the in-flight research state in case _execute_research
        # bailed without reaching its finally.
        try:
            RS.clear(user_id)
        except Exception:
            pass
        # Close the browser session page so the next scenario starts clean.
        try:
            req = urllib.request.Request(
                f"{base_url}/api/browser/session",
                data=json.dumps({"action": "close"}).encode(),
                headers={"content-type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass

    return record


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

async def main() -> int:
    parser = argparse.ArgumentParser(description="Research-mode benchmark runner")
    parser.add_argument(
        "--scenario", type=int, default=0,
        help="Run a single scenario by id (1-based). Default: run all.",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8005",
        help="Browser sidecar URL (BASE_PORT+5 by default).",
    )
    args = parser.parse_args()

    from benchmark.research.scenarios import ALL

    if args.scenario:
        scenarios = [s for s in ALL if s["id"] == args.scenario]
        if not scenarios:
            print(f"unknown scenario id: {args.scenario}")
            return 1
    else:
        scenarios = ALL

    results: List[Dict[str, Any]] = []
    for sc in scenarios:
        rec = await run_one(sc, args.base_url)
        results.append(rec)

    # Final tally
    print(f"\n{'='*72}")
    print("RESEARCH BENCHMARK SUMMARY")
    print(f"{'='*72}")
    pass_count = sum(1 for r in results if r.get("verdict", {}).get("verdict") == "PASS")
    partial_count = sum(1 for r in results if r.get("verdict", {}).get("verdict") == "PARTIAL")
    fail_count = sum(1 for r in results if r.get("verdict", {}).get("verdict") == "FAIL")
    for r in results:
        sc = r["scenario"]
        v = r.get("verdict", {})
        elapsed = r.get("elapsed_s", "?")
        print(f"  [{v.get('verdict','?'):7}] s{sc['id']} {sc['name'][:55]:55} {elapsed}s")
    print(f"\nTotals: {pass_count} pass · {partial_count} partial · {fail_count} fail "
          f"({len(results)} scenarios)")

    # Persist
    results_dir = os.path.join(os.path.dirname(__file__), "..", "results")
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"research{('_s' + str(args.scenario)) if args.scenario else ''}_{ts}.json"
    fpath = os.path.abspath(os.path.join(results_dir, fname))
    with open(fpath, "w") as f:
        json.dump({
            "ran_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "pass": pass_count,
                "partial": partial_count,
                "fail": fail_count,
                "total": len(results),
            },
            "results": results,
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {fpath}")

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
