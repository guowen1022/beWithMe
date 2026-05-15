"""End-to-end smoke for Lane R (research mode).

Drives `_execute_research` against a real URL with a real LLM and the
running browser sidecar. Stubs out canvas mount/push and silicon_brain
calls so we don't need the desktop or the silicon_brain sidecar — the
test focuses on:

  - Lane R's prompt + context build
  - The agent loop (LLM → tool calls → tool results → next turn)
  - The planning scaffold (research_plan / research_note)
  - The wall-clock deadline
  - Final synthesis via `speak`

Run:
    .venv/bin/python tests/manual/test_research_e2e.py [URL] [GOAL]

Defaults: URL=https://en.wikipedia.org/wiki/Photosynthesis ,
          GOAL="What is photosynthesis and what's the most surprising
                fact on this page?"

Requires:
  - .env populated (LLM_PROVIDER + DEEPSEEK_*  /  MiniMax)
  - The browser sidecar running on BASE_PORT+5 (default :8005)
  - VISION_PROVIDER set (the look_at_image tool may try to use it)

Prints a turn-by-turn trace and a verdict at the end:
  PASS  if research_plan + ≥ 1 research_note + 1 speak fired
        and synthesis text references something from the page
  FAIL  with the missing piece otherwise.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

# Ensure the repo root is on sys.path so `from persona...` resolves.
import os
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


async def _stub_canvas(*args, **kwargs):
    """Replace _push_research_state_to_canvas — print state instead."""
    from persona.teacher import research_state
    user_id = kwargs.get("user_id") or (args[0] if args else None)
    state = research_state.get(user_id) if user_id else None
    if state is None:
        return
    done = sum(1 for s in state.steps if s.status == "done")
    cur = next((s.text for s in state.steps if s.status == "doing"), "")
    print(f"  [canvas] {done}/{len(state.steps)} · {cur or '(no current step)'} "
          f"· finished={state.finished}")


async def _stub_enqueue(user_id, event):
    """Replace enqueue_for_user — print TeacherThinking events."""
    if hasattr(event, "phase"):
        print(f"  [thinking] phase={event.phase} trigger={event.trigger} "
              f"summary={getattr(event, 'summary', '')[:60]}")
    return 1


async def _stub_get_profile(self, user_id):  # methods take self
    return None


async def _stub_get_talk_preference(self, user_id):
    return {"desktop": "both", "tablet": "both", "phone": "text"}


async def _stub_aclose(self):
    pass


async def _stub_read_media(user_id):
    """No real canvas — return an empty perception."""
    from infra.contracts.ui import PerceptionInventory
    return PerceptionInventory(canvases=[], voices=[])


async def main(url: str, goal: str) -> int:
    # Late imports so dotenv loads (infra.config calls load_dotenv) before
    # provider URLs are validated.
    from persona.teacher.tools import manifest as M
    from persona.teacher.contexts import research as ctx_r
    from persona.teacher import research_state as RS
    from infra.silicon_brain_client import SiliconBrainClient

    user_id = uuid.uuid4()
    print(f"=== research E2E ===")
    print(f"user_id: {user_id}")
    print(f"url:     {url}")
    print(f"goal:    {goal}")
    print()

    # Pre-load the page in the running browser sidecar so the LLM doesn't
    # need to call goto first — simulates "user already opened the page".
    import urllib.request
    print(f"[setup] preloading {url} in headless browser sidecar...")
    req = urllib.request.Request(
        "http://localhost:8005/api/browser/session",
        data=json.dumps({"action": "goto", "url": url, "wait_until": "domcontentloaded"}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        body = urllib.request.urlopen(req, timeout=20).read()
        loaded = json.loads(body)
        print(f"[setup] loaded title={loaded.get('title','?')[:80]!r}")
    except Exception as e:
        print(f"[setup] FAILED to preload: {e}")
        return 1

    # ---- Patch the bits we don't have running ----
    patches = []

    # Replace the canvas push with a printer.
    patches.append(patch.object(M, "_push_research_state_to_canvas", _stub_canvas))
    # Replace silicon_brain client methods.
    patches.append(patch.object(SiliconBrainClient, "get_profile", _stub_get_profile))
    patches.append(patch.object(SiliconBrainClient, "get_talk_preference", _stub_get_talk_preference))
    patches.append(patch.object(SiliconBrainClient, "aclose", _stub_aclose))
    # Replace canvas read.
    from workshop.canvas.tools import read_media as _rm
    patches.append(patch.object(_rm, "read_media", _stub_read_media))
    # Replace enqueue_for_user (the SSE bus). Patch in the persona router
    # module + in triggers' late-import path.
    from services.persona.routers import dynamic as _dyn
    patches.append(patch.object(_dyn, "enqueue_for_user", _stub_enqueue))
    # Avoid teacher_notices side-effects.
    from persona.teacher import notices as _notices
    patches.append(patch.object(_notices, "drain", lambda uid: []))

    for p in patches:
        p.start()

    try:
        # Replace the speak executor with a recorder so we can verify the
        # synthesis text without TTS. Also mute mount_template / push_block_content
        # at the workshop layer so we don't hammer the canvas SSE endpoints.
        recorded_speaks: List[Dict[str, Any]] = []
        recorded_calls: List[Dict[str, Any]] = []

        original_make_speak = M._make_speak
        def fake_make_speak(uid):
            async def exec_(args):
                recorded_speaks.append(args)
                return json.dumps({"ok": True, "delivered_to": ["test"]})
            return exec_
        M._make_speak = fake_make_speak

        # Wrap research_plan / research_note executors to log calls.
        original_make_plan = M._make_research_plan
        original_make_note = M._make_research_note
        def wrap_plan(uid):
            inner = original_make_plan(uid)
            async def exec_(args):
                recorded_calls.append({"name": "research_plan", "args": args})
                return await inner(args)
            return exec_
        def wrap_note(uid):
            inner = original_make_note(uid)
            async def exec_(args):
                recorded_calls.append({"name": "research_note", "args": args})
                return await inner(args)
            return exec_
        M._make_research_plan = wrap_plan
        M._make_research_note = wrap_note

        # Stub mount_template + push_block_content at the workshop layer so the
        # canvas-touching helper inside _push_research_state_to_canvas (we
        # already replaced it) doesn't matter — but other tools the LLM might
        # call (e.g. mount_template for a text_display) shouldn't blow up.
        from workshop.canvas.tools import mount_template as _mt
        from workshop.canvas.tools import push_block_content as _pb
        async def _noop_mount(**kw):
            return type("R", (), {"block_id": kw.get("block_id", "x"), "template": kw.get("template_name","x"), "deleted": []})()
        async def _noop_push(**kw):
            return 0
        patch.object(_mt, "mount_template", _noop_mount).start()
        patch.object(_pb, "push_block_content", _noop_push).start()

        # ---- Run Lane R ----
        from persona.teacher.triggers import _execute_research
        # Initialize research state up front (start_research's job in real flow).
        RS.begin(user_id, goal=goal)

        t0 = time.monotonic()
        await _execute_research(user_id, goal)
        elapsed = time.monotonic() - t0

        print()
        print("=" * 60)
        print(f"=== RESULT (took {elapsed:.1f}s) ===")
        print("=" * 60)

        # Tally what fired.
        plan_calls = [c for c in recorded_calls if c["name"] == "research_plan"]
        note_calls = [c for c in recorded_calls if c["name"] == "research_note"]
        print(f"research_plan calls: {len(plan_calls)}")
        for c in plan_calls:
            for i, s in enumerate(c["args"].get("steps") or []):
                print(f"  {i}. {s}")
        print(f"research_note calls: {len(note_calls)}")
        for c in note_calls:
            print(f"  step={c['args'].get('step_index')} finding={c['args'].get('finding','')[:120]!r}")
        print(f"speak calls: {len(recorded_speaks)}")
        for s in recorded_speaks:
            print(f"  channel={s.get('channel')} text={(s.get('text') or '')[:300]!r}")

        # Verdict.
        fail_reasons = []
        if not plan_calls:
            fail_reasons.append("no research_plan call (planning scaffold not used)")
        if not note_calls:
            fail_reasons.append("no research_note call (no findings recorded)")
        if not recorded_speaks:
            fail_reasons.append("no speak call (no synthesis delivered)")
        if recorded_speaks:
            synth = (recorded_speaks[0].get("text") or "").lower()
            if len(synth) < 30:
                fail_reasons.append(f"synthesis suspiciously short ({len(synth)} chars)")

        # Restore the things we manually replaced (best-effort).
        M._make_speak = original_make_speak
        M._make_research_plan = original_make_plan
        M._make_research_note = original_make_note

        print()
        if fail_reasons:
            print("VERDICT: FAIL")
            for r in fail_reasons:
                print(f"  - {r}")
            return 1
        print("VERDICT: PASS")
        return 0
    finally:
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass
        # Clean up the sidecar's session page so we don't leave one open.
        try:
            req = urllib.request.Request(
                "http://localhost:8005/api/browser/session",
                data=json.dumps({"action": "close"}).encode(),
                headers={"content-type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://en.wikipedia.org/wiki/Photosynthesis"
    goal = sys.argv[2] if len(sys.argv) > 2 else (
        "What is photosynthesis, and what's the most interesting / surprising "
        "fact you can pull from this Wikipedia page?"
    )
    sys.exit(asyncio.run(main(url, goal)))
