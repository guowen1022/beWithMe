"""Voice benchmark runner.

Measures end-to-end response latency for the voice-in → voice-out path:

    user audio ──▶ /api/transcribe ──▶ transcript
                                       │
                                       ▼
                              /api/ask/stream ──▶ phase_timings_ms
                                       │            (incl. first_speak_call_ms)
                                       ▼
                                 speak() text
                                       │
                                       ▼
                              /api/speak/stream ──▶ first audio byte

Per question we record:
- stt_ms          — POST /api/transcribe round-trip
- ask_ttft_ms     — request start → first SSE `token` event
- ask_answer_ms   — request start → terminal `answer` event
- phase_timings   — backend-emitted dict (12 context sub-phases + LLM TTFT
                    + first_speak_call_ms + tool_iterations)
- tts_first_byte_ms — POST /api/speak/stream → first PCM byte at client
- tts_first_chunk_server_ms — X-First-Chunk-Ms header (eager-synthesis time)
- ttfa_ms         — Time-To-First-Audio estimate:
                      stt_ms + first_speak_call_ms + tts_first_byte_ms
                    (falls back to stt_ms + ask_answer_ms + tts_first_byte_ms
                    when the persona didn't call speak() this turn)
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

from benchmark.voice.scenarios import ALL_SCENARIOS
from benchmark.voice.audio_fixtures import ensure_audio


def _h(user_id: str) -> dict:
    # Match the frontend's headers — X-Device-Class=desktop so the persona
    # picks the "voice" channel from talk_preference.desktop.
    return {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
        "X-Device-Class": "desktop",
    }


async def _ensure_user(client: httpx.AsyncClient, username: str) -> str:
    """Create or fetch the benchmark user; return its UUID."""
    resp = await client.post("/api/users", json={"username": username})
    if resp.status_code == 409:
        resp = await client.get("/api/users")
        resp.raise_for_status()
        users = resp.json()
        return next(u["id"] for u in users if u["username"] == username)
    resp.raise_for_status()
    return resp.json()["id"]


async def _setup_user(client: httpx.AsyncClient, scenario: dict) -> str:
    username = f"voice_bench_{scenario['name'][:20].lower().replace(' ', '_')}"
    user_id = await _ensure_user(client, username)
    h = _h(user_id)
    await client.put("/api/profile", headers=h,
                     json={"self_description": scenario["profile"]})
    await client.put("/api/talk-preference", headers=h,
                     json=scenario["talk_preference"])
    print(f"[user] {username} (id={user_id[:8]}…)")
    return user_id


async def _post_transcribe(client: httpx.AsyncClient, headers: dict,
                            audio_path: Path) -> tuple[str, float]:
    """POST a WAV; return (transcript, stt_ms)."""
    t0 = time.perf_counter()
    files = {
        "file": (audio_path.name, audio_path.read_bytes(), "audio/wav"),
    }
    data = {"language": "en"}
    # httpx files= won't accept headers like Content-Type: application/json —
    # strip it for multipart.
    h = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    resp = await client.post("/api/transcribe", headers=h, files=files, data=data)
    resp.raise_for_status()
    body = resp.json()
    stt_ms = round((time.perf_counter() - t0) * 1000, 2)
    return body.get("text", ""), stt_ms


async def _ask_stream(client: httpx.AsyncClient, headers: dict, payload: dict
                       ) -> dict[str, Any]:
    """POST /api/ask/stream; stream SSE. Return timings + phase_timings."""
    t0 = time.perf_counter()
    ttft_ms: float | None = None
    answer_ms: float | None = None
    answer_text = ""
    phase_timings: dict = {}

    async with client.stream("POST", "/api/ask/stream",
                              headers=headers, json=payload,
                              timeout=120.0) as resp:
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

    return {
        "ask_ttft_ms": ttft_ms,
        "ask_answer_ms": answer_ms,
        "answer_text": answer_text,
        "phase_timings": phase_timings,
    }


async def _tts_first_byte(client: httpx.AsyncClient, headers: dict,
                           text: str) -> tuple[float | None, float | None]:
    """POST /api/speak/stream; return (tts_first_byte_ms, server_first_chunk_ms).

    server_first_chunk_ms is the X-First-Chunk-Ms header — Kokoro's eager
    first-sentence synthesis time, server-side. The client-side first-byte
    includes a small network round-trip on top.
    """
    text = (text or "").strip()
    if not text:
        return None, None
    # Cap at 1000 chars — first-chunk is sentence-split, longer text only
    # affects later chunks which we don't measure.
    payload = {"text": text[:1000]}
    t0 = time.perf_counter()
    async with client.stream("POST", "/api/speak/stream",
                              headers=headers, json=payload,
                              timeout=60.0) as resp:
        resp.raise_for_status()
        server_first = resp.headers.get("X-First-Chunk-Ms")
        server_first_ms = float(server_first) if server_first else None
        first_byte_ms: float | None = None
        async for chunk in resp.aiter_raw():
            if chunk:
                first_byte_ms = round((time.perf_counter() - t0) * 1000, 2)
                break
        # We don't need to drain the rest — closing the stream is enough.
    return first_byte_ms, server_first_ms


async def run_question(
    client: httpx.AsyncClient,
    user_id: str,
    question: str,
    session_id: str,
    q_idx: int,
) -> dict[str, Any]:
    headers = _h(user_id)

    # Stage 0: ensure we have a WAV for this question text.
    audio_path = await ensure_audio(client, question, headers=headers)

    # Stage 1: STT
    transcript, stt_ms = await _post_transcribe(client, headers, audio_path)

    # Stage 2: Ask
    ask = await _ask_stream(client, headers, {
        "question": transcript or question,
        "session_id": session_id,
    })

    # Stage 3: TTS — prefer the persona's actual spoken text. Falls back to
    # the full answer if the persona declined to call speak() this turn.
    pt = ask["phase_timings"]
    speak_text = pt.get("first_speak_text") or ask["answer_text"]
    speak_called = bool(pt.get("first_speak_text"))
    tts_first_byte_ms, tts_first_chunk_server_ms = await _tts_first_byte(
        client, headers, speak_text
    )

    # Stage 4: TTFA estimate. Three possible audio-trigger sources, in
    # priority order:
    #   1. auto_speak_first_ms — backend's sentence-buffer fired TTS as
    #      soon as the LLM completed its first sentence (voice-mode path).
    #   2. first_speak_call_ms — the LLM explicitly called the speak()
    #      tool (mid-stream tool-call path).
    #   3. ask_answer_ms — full LLM response, no streaming-to-TTS at all.
    auto_speak_first_ms = pt.get("auto_speak_first_ms")
    auto_speak_count = pt.get("auto_speak_count") or 0
    first_speak_call_ms = pt.get("first_speak_call_ms")
    audio_trigger_ms: float | None = None
    if auto_speak_first_ms is not None:
        audio_trigger_ms = auto_speak_first_ms
    elif first_speak_call_ms is not None:
        audio_trigger_ms = first_speak_call_ms
    elif ask["ask_answer_ms"] is not None:
        audio_trigger_ms = ask["ask_answer_ms"]

    if audio_trigger_ms is not None and tts_first_byte_ms is not None:
        ttfa_ms = round(stt_ms + audio_trigger_ms + tts_first_byte_ms, 2)
    else:
        ttfa_ms = None

    audio_fired = speak_called or auto_speak_count > 0

    record = {
        "q_idx": q_idx,
        "question": question,
        "transcript": transcript,
        "speak_called": speak_called,
        "audio_fired": audio_fired,
        "auto_speak_count": auto_speak_count,
        "audio_trigger_ms": audio_trigger_ms,
        "stt_ms": stt_ms,
        "ask_ttft_ms": ask["ask_ttft_ms"],
        "ask_answer_ms": ask["ask_answer_ms"],
        "tts_first_byte_ms": tts_first_byte_ms,
        "tts_first_chunk_server_ms": tts_first_chunk_server_ms,
        "ttfa_ms": ttfa_ms,
        "phase_timings_ms": pt,
        "answer_chars": len(ask["answer_text"]),
    }

    # Glyph legend: ▶ = LLM called speak() tool; ♪ = auto-speak via
    # sentence buffer; · = no audio fired this turn.
    if speak_called:
        mark = "▶"
    elif auto_speak_count > 0:
        mark = "♪"
    else:
        mark = "·"
    ttfa_str = f"{ttfa_ms:.0f}ms" if ttfa_ms is not None else "n/a"
    trig_str = f"trig={audio_trigger_ms:.0f}" if audio_trigger_ms is not None else "trig=?"
    print(
        f"  Q{q_idx} {mark} stt={stt_ms:.0f} "
        f"{trig_str} "
        f"tts_fb={tts_first_byte_ms or 0:.0f} "
        f"sents={auto_speak_count} "
        f"=> TTFA={ttfa_str}",
        flush=True,
    )
    return record


def _stats(values: list[float]) -> dict[str, float]:
    """Mean / p50 / p95 / min / max. Empty input → all None."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "p50": None, "p95": None,
                "min": None, "max": None}
    vals.sort()
    if len(vals) >= 4:
        # statistics.quantiles uses method='exclusive' by default which
        # complains on n=1. Guard accordingly.
        q = statistics.quantiles(vals, n=20, method="inclusive")
        p95 = q[18]  # 95th percentile from 20-quantile split
    else:
        p95 = vals[-1]
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "p50": round(statistics.median(vals), 2),
        "p95": round(p95, 2),
        "min": round(vals[0], 2),
        "max": round(vals[-1], 2),
    }


def _print_summary(records: list[dict]) -> dict:
    """Print + return aggregate stats for each phase."""
    def col(key: str) -> list[float]:
        return [r[key] for r in records if r.get(key) is not None]

    def pcol(key: str) -> list[float]:
        return [r["phase_timings_ms"].get(key)
                for r in records
                if r.get("phase_timings_ms")
                and r["phase_timings_ms"].get(key) is not None]

    top_level = {
        "stt_ms": _stats(col("stt_ms")),
        "ask_ttft_ms": _stats(col("ask_ttft_ms")),
        "ask_answer_ms": _stats(col("ask_answer_ms")),
        "audio_trigger_ms": _stats(col("audio_trigger_ms")),
        "tts_first_byte_ms": _stats(col("tts_first_byte_ms")),
        "tts_first_chunk_server_ms": _stats(col("tts_first_chunk_server_ms")),
        "ttfa_ms": _stats(col("ttfa_ms")),
    }

    audio_fire_rate = sum(1 for r in records if r.get("audio_fired")) / max(len(records), 1)

    phase_keys = [
        "ctx_profile_ms",
        "ctx_talk_pref_ms",
        "ctx_user_profile_ms",
        "ctx_concepts_ms",
        "ctx_graph_ms",
        "ctx_embed_ms",
        "ctx_boost_embed_ms",
        "ctx_doc_search_ms",
        "ctx_past_summaries_ms",
        "ctx_history_ms",
        "ctx_canvas_ms",
        "ctx_build_prompt_ms",
        "context_total_ms",
        "llm_ttft_ms",
        "first_speak_call_ms",
        "auto_speak_first_ms",
        "auto_speak_count",
        "llm_done_ms",
        "total_ms",
    ]
    phase_stats = {k: _stats(pcol(k)) for k in phase_keys}

    print("\n" + "=" * 76)
    print("VOICE BENCHMARK SUMMARY")
    print("=" * 76)
    print(f"{'phase':<32} {'mean':>10} {'p50':>10} {'p95':>10} {'n':>4}")
    print("-" * 76)
    print(f"audio-fire rate: {audio_fire_rate*100:.0f}% ({sum(1 for r in records if r.get('audio_fired'))}/{len(records)})")
    print("-" * 76)
    for label, key in (
        ("stt_ms", "stt_ms"),
        ("ask_ttft_ms", "ask_ttft_ms"),
        ("ask_answer_ms (full LLM)", "ask_answer_ms"),
        ("audio_trigger_ms", "audio_trigger_ms"),
        ("tts_first_byte_ms", "tts_first_byte_ms"),
        ("tts_first_chunk_server_ms", "tts_first_chunk_server_ms"),
        ("TTFA_ms (end-to-end)", "ttfa_ms"),
    ):
        s = top_level[key]
        if s["n"] == 0:
            print(f"{label:<32} {'-':>10} {'-':>10} {'-':>10} {0:>4}")
            continue
        print(f"{label:<32} {s['mean']:>10.1f} {s['p50']:>10.1f} {s['p95']:>10.1f} {s['n']:>4}")

    print("\n" + "-" * 76)
    print("Backend phase breakdown (from /api/ask/stream phase_timings_ms):")
    print("-" * 76)
    for k in phase_keys:
        s = phase_stats[k]
        if s["n"] == 0:
            continue
        print(f"  {k:<30} {s['mean']:>10.1f} {s['p50']:>10.1f} {s['p95']:>10.1f} {s['n']:>4}")

    return {"top_level": top_level, "phase_timings": phase_stats}


async def run_scenario(scenario: dict, base_url: str, warmup: bool = True) -> dict:
    print("\n" + "=" * 76)
    print(f"VOICE SCENARIO: {scenario['name']}")
    print("=" * 76)

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        user_id = await _setup_user(client, scenario)
        session_id = str(uuid.uuid4())

        # Warmup turn: discarded, just lets Kokoro / Whisper / DeepSeek
        # warm up so the measured numbers aren't dominated by cold starts.
        if warmup and scenario["questions"]:
            print("\n[warmup] discarding first question (cold-start)")
            try:
                await run_question(client, user_id, scenario["questions"][0],
                                    session_id, q_idx=0)
            except Exception as e:
                print(f"[warmup] failed: {e}")

        records = []
        print(f"\n[run] {len(scenario['questions'])} questions, session={session_id[:8]}…")
        for i, q in enumerate(scenario["questions"], start=1):
            try:
                rec = await run_question(client, user_id, q, session_id, q_idx=i)
                records.append(rec)
            except Exception as e:
                print(f"  Q{i}: ERROR {type(e).__name__}: {e}", flush=True)
                records.append({"q_idx": i, "question": q, "error": str(e)})

        summary = _print_summary([r for r in records if "error" not in r])

        return {
            "scenario": scenario["name"],
            "user_id": user_id,
            "session_id": session_id,
            "questions": records,
            "summary": summary,
        }


async def main():
    parser = argparse.ArgumentParser(
        description="beWithMe voice-to-voice response-time benchmark"
    )
    parser.add_argument("--scenario", type=int, default=1,
                        help="Scenario number (1-based)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000",
                        help="Shell sidecar base URL")
    parser.add_argument("--no-warmup", action="store_true",
                        help="Skip the cold-start warmup turn")
    args = parser.parse_args()

    idx = args.scenario - 1
    if idx < 0 or idx >= len(ALL_SCENARIOS):
        print(f"Invalid scenario {args.scenario}. Available: 1-{len(ALL_SCENARIOS)}")
        return

    scenario = ALL_SCENARIOS[idx]
    result = await run_scenario(scenario, args.base_url, warmup=not args.no_warmup)

    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filepath = results_dir / f"voice_{scenario['name']}_{ts}.json"
    filepath.write_text(json.dumps(result, indent=2))
    print(f"\nResults saved to: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
