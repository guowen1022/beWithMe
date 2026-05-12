"""Orchestration-accuracy benchmark runner.

For each scenario:
  1. POST /api/ask/stream with the question.
  2. Stream the SSE response; collect every `tool_call` event the
     persona emits (its tool invocations for the turn).
  3. Grade via the scenario's `predicate(name, args)`:
       - `expected_tool == "none"`: PASS iff no tool calls observed.
       - otherwise: PASS iff any tool call satisfies the predicate.
  4. Also record latency (ask_ttft_ms, ask_answer_ms, ttfa proxy).

Run modes (`--mode`):
  - `off` (default): Lane A's current setting — DeepSeek thinking OFF.
  - `on`: thinking ON via the X-Lane-Thinking header.
  - `both`: runs each scenario in both modes back-to-back.

Saves a single JSON to benchmark/orchestration/results/ summarising
pass-rate, per-scenario diff, and aggregate latency for each mode.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from benchmark.orchestration.scenarios import SCENARIOS, _NO_OP_TOOLS


def _h(user_id: str, *, thinking: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
        "X-Device-Class": "desktop",
    }
    if thinking is not None:
        headers["X-Lane-Thinking"] = thinking
    return headers


async def _ensure_user(client: httpx.AsyncClient, username: str) -> str:
    resp = await client.post("/api/users", json={"username": username})
    if resp.status_code == 409:
        resp = await client.get("/api/users")
        resp.raise_for_status()
        return next(
            u["id"] for u in resp.json() if u["username"] == username
        )
    resp.raise_for_status()
    return resp.json()["id"]


async def _ask_stream(
    client: httpx.AsyncClient, headers: dict, payload: dict
) -> dict[str, Any]:
    """POST /api/ask/stream; return all tool_calls + timings + phases."""
    t0 = time.perf_counter()
    ttft_ms: float | None = None
    answer_ms: float | None = None
    tool_calls: list[dict] = []
    answer_text = ""
    phase_timings: dict = {}

    async with client.stream(
        "POST",
        "/api/ask/stream",
        headers=headers,
        json=payload,
        timeout=180.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "token" and ttft_ms is None:
                ttft_ms = round((time.perf_counter() - t0) * 1000, 2)
            elif etype == "answer":
                answer_ms = round((time.perf_counter() - t0) * 1000, 2)
                answer_text = evt.get("answer", "")
                phase_timings = evt.get("phase_timings_ms") or {}
            elif etype == "tool_call":
                tool_calls.append({
                    "name": evt.get("name") or "",
                    "arguments": evt.get("arguments") or {},
                })

    return {
        "ask_ttft_ms": ttft_ms,
        "ask_answer_ms": answer_ms,
        "answer_text": answer_text,
        "phase_timings": phase_timings,
        "tool_calls": tool_calls,
    }


def _grade(scenario: dict, tool_calls: list[dict]) -> tuple[bool, str]:
    """Return (passed, reason).

    No-op tools (read_media, look_at_image) are filtered before grading
    — they're harmless context-gathering calls the persona may make
    before its actual decision.
    """
    real_calls = [tc for tc in tool_calls if tc["name"] not in _NO_OP_TOOLS]
    expected = scenario["expected_tool"]
    if expected == "none":
        if not real_calls:
            return True, "no real tool calls (as expected)"
        names = [tc["name"] for tc in real_calls]
        return False, f"expected no tool, got {names}"

    predicate = scenario["predicate"]
    for tc in real_calls:
        try:
            if predicate(tc["name"], tc["arguments"]):
                return True, f"matched {tc['name']}({list(tc['arguments'].keys())})"
        except Exception as e:
            return False, f"predicate error: {e}"
    if not real_calls:
        return False, f"no tool calls; expected {expected}"
    names = [tc["name"] for tc in real_calls]
    return False, f"got {names}, none matched predicate for {expected}"


async def run_one_scenario(
    client: httpx.AsyncClient,
    user_id: str,
    scenario: dict,
    thinking: str,
) -> dict[str, Any]:
    headers = _h(user_id, thinking=thinking)
    session_id = str(uuid.uuid4())  # fresh session per scenario; no history
    result = await _ask_stream(client, headers, {
        "question": scenario["question"],
        "session_id": session_id,
    })
    passed, reason = _grade(scenario, result["tool_calls"])

    pt = result["phase_timings"]
    return {
        "id": scenario["id"],
        "question": scenario["question"],
        "expected_tool": scenario["expected_tool"],
        "thinking": thinking,
        "passed": passed,
        "reason": reason,
        "tool_calls": result["tool_calls"],
        "ask_ttft_ms": result["ask_ttft_ms"],
        "ask_answer_ms": result["ask_answer_ms"],
        "llm_ttft_ms": pt.get("llm_ttft_ms"),
        "llm_done_ms": pt.get("llm_done_ms"),
        "auto_speak_count": pt.get("auto_speak_count") or 0,
        "answer_chars": len(result["answer_text"]),
        "phase_timings_ms": pt,
    }


async def run_mode(
    client: httpx.AsyncClient, user_id: str, mode: str
) -> list[dict]:
    """Run all scenarios in a single mode (`on` or `off`)."""
    print(f"\n{'=' * 76}")
    print(f"MODE: thinking={mode}")
    print(f"{'=' * 76}")
    records = []
    for i, scenario in enumerate(SCENARIOS, start=1):
        try:
            rec = await run_one_scenario(client, user_id, scenario, mode)
            mark = "✓" if rec["passed"] else "✗"
            ttft = rec["llm_ttft_ms"] or 0
            done = rec["llm_done_ms"] or rec["ask_answer_ms"] or 0
            print(
                f"  {i:>2}. {mark} {scenario['id']:<28} "
                f"ttft={ttft:>6.0f}ms done={done:>6.0f}ms  {rec['reason']}",
                flush=True,
            )
        except Exception as e:
            print(f"  {i:>2}. ! {scenario['id']:<28} ERROR {type(e).__name__}: {e}", flush=True)
            rec = {
                "id": scenario["id"],
                "question": scenario["question"],
                "thinking": mode,
                "error": f"{type(e).__name__}: {e}",
                "passed": False,
            }
        records.append(rec)
    return records


def _summarise(label: str, records: list[dict]) -> dict:
    valid = [r for r in records if "error" not in r]
    passed = [r for r in valid if r.get("passed")]
    ttfts = [r.get("llm_ttft_ms") for r in valid if r.get("llm_ttft_ms") is not None]
    dones = [r.get("llm_done_ms") for r in valid if r.get("llm_done_ms") is not None]
    return {
        "label": label,
        "total": len(records),
        "passed": len(passed),
        "failed": len(records) - len(passed),
        "pass_rate": round(len(passed) / max(len(records), 1) * 100, 1),
        "mean_llm_ttft_ms": round(statistics.mean(ttfts), 1) if ttfts else None,
        "mean_llm_done_ms": round(statistics.mean(dones), 1) if dones else None,
    }


def _print_summary(off_records: list[dict] | None, on_records: list[dict] | None):
    print("\n" + "=" * 76)
    print("ORCHESTRATION BENCHMARK — SUMMARY")
    print("=" * 76)
    for label, recs in (("thinking=off", off_records), ("thinking=on", on_records)):
        if recs is None:
            continue
        s = _summarise(label, recs)
        print(
            f"  {label:<14} {s['passed']}/{s['total']} "
            f"pass ({s['pass_rate']:>5.1f}%)   "
            f"llm_ttft mean={s['mean_llm_ttft_ms']}ms   "
            f"llm_done mean={s['mean_llm_done_ms']}ms"
        )

    if off_records is not None and on_records is not None:
        print("\n  per-scenario diff (off / on):")
        on_by_id = {r["id"]: r for r in on_records if "error" not in r}
        for off in off_records:
            on = on_by_id.get(off["id"])
            if on is None:
                continue
            off_mark = "✓" if off.get("passed") else "✗"
            on_mark = "✓" if on.get("passed") else "✗"
            diff = ""
            if off.get("passed") != on.get("passed"):
                diff = "  ← DIFFERS"
            print(
                f"    {off['id']:<28} off={off_mark} on={on_mark}{diff}"
            )


async def main():
    parser = argparse.ArgumentParser(
        description="Persona tool-orchestration accuracy benchmark"
    )
    parser.add_argument(
        "--mode",
        choices=["off", "on", "both"],
        default="both",
        help="Which thinking mode(s) to run. Default: both.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Shell sidecar base URL",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=180.0) as client:
        # Use a stable benchmark user; orchestration tests are stateless
        # per-scenario (fresh session_id) so a shared user is fine.
        user_id = await _ensure_user(client, "orch_bench_user")
        print(f"[user] orch_bench_user (id={user_id[:8]}…)")
        await client.put(
            "/api/profile",
            headers=_h(user_id),
            json={"self_description": "Benchmark user for orchestration tests."},
        )
        await client.put(
            "/api/talk-preference",
            headers=_h(user_id),
            json={"desktop": "both", "tablet": "both", "phone": "text"},
        )

        off_records = None
        on_records = None
        if args.mode in ("off", "both"):
            off_records = await run_mode(client, user_id, "off")
        if args.mode in ("on", "both"):
            on_records = await run_mode(client, user_id, "on")

    _print_summary(off_records, on_records)

    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = {
        "ts": ts,
        "mode": args.mode,
        "off": off_records,
        "on": on_records,
    }
    filepath = results_dir / f"orchestration_{args.mode}_{ts}.json"
    filepath.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nResults saved to: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
