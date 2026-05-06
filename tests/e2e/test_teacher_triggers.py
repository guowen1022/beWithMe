"""E2E for event-driven teacher triggers (Phase D).

Covers:
  * cache edge-detect: completed:false → true emits BlockCompletedEvent;
    re-true→true does not refire.
  * trigger orchestrator: idle path fires immediately; cooldown buffers
    later events into one trailing fire.
  * end-to-end: trigger drives the teacher tool loop and emits
    `teacher-thinking` SSE events on the user's dynamic stream.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import httpx
import pytest


# ---- in-process: cache edge detection -------------------------------------


def test_completed_edge_fires_once(test_user_id: str):
    """Setting completed=true once fires BlockCompletedEvent. Re-true→true
    edges don't refire."""
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockCompletedEvent, BlockState

    perc_cache._reset_for_tests()
    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()

    fires: list = []

    async def listener(event):
        if isinstance(event, BlockCompletedEvent):
            fires.append(event)

    async def drive():
        unsub = perc_cache.subscribe(listener)
        try:
            # First report: completed=false. No fire.
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="uploading", completed=False),
            )
            await asyncio.sleep(0.05)
            # Edge: false → true. Should fire.
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready", completed=True),
            )
            await asyncio.sleep(0.05)
            # Stay true. No re-fire.
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready (re-emit)", completed=True),
            )
            await asyncio.sleep(0.05)
        finally:
            unsub()

    asyncio.run(drive())

    assert len(fires) == 1, [(e.block_id, e.state.content) for e in fires]
    assert fires[0].block_id == "upload-file"
    assert fires[0].state.completed is True


# ---- in-process: trigger orchestrator state machine -----------------------


def test_orchestrator_fires_idle_immediately(monkeypatch, test_user_id: str):
    """Idle user → completion event fires the synthesized turn right away."""
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockCompletedEvent, BlockState
    from persona.teacher import triggers as triggers

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()

    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()

    # Replace _execute_turn with a recorder so we don't actually run the
    # teacher LLM (this test pins the orchestrator's logic, not the LLM
    # path; that's the next test). The events list contains
    # PerceptionEventSummary objects.
    fired: list = []

    async def fake_execute_turn(user_id, bucket, events):
        fired.append((bucket, [(e.block_id, e.event_type) for e in events]))

    monkeypatch.setattr(triggers, "_execute_turn", fake_execute_turn)

    async def drive():
        triggers.install()
        try:
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready", completed=True),
            )
            # Allow the cache's _schedule_fire task + orchestrator's
            # _run_turn task to run.
            await asyncio.sleep(0.2)
        finally:
            triggers.uninstall()

    asyncio.run(drive())
    # Both a change event (state recorded) AND a completed edge fire here.
    # We assert the completion fired with the right shape; the change event
    # may or may not fire depending on event ordering — it's fine either way.
    completed_fires = [f for f in fired if f[0] == "completed"]
    assert completed_fires == [("completed", [("upload-file", "completed")])], fired


def test_orchestrator_coalesces_during_cooldown(monkeypatch, test_user_id: str):
    """Three completions within the cooldown window → 2 fires on the
    `completed` bucket:
       (a) the immediate one for the first event,
       (b) one trailing fire at cooldown-end carrying events 2 + 3.
    """
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockState
    from persona.teacher import triggers as triggers

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    # Tighten the cooldown to keep the test fast. The orchestrator now
    # uses per-event-type budgets; rebind the completed-bucket budget.
    monkeypatch.setitem(
        triggers._BUDGETS,
        "completed",
        triggers._Budget(cooldown_s=0.5, max_tokens=1500, trigger_label="block-completed"),
    )

    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()

    fired: list = []

    async def fake_execute_turn(user_id, bucket, events):
        if bucket == "completed":
            fired.append([(e.block_id, e.content) for e in events])

    monkeypatch.setattr(triggers, "_execute_turn", fake_execute_turn)

    async def drive():
        triggers.install()
        try:
            # Three rapid completions, each from a different block id so
            # the cache doesn't dedupe them.
            for i, bid in enumerate(["upload-file", "passage-reader", "pdf-reader"]):
                perc_cache.record_block_state(
                    user_id=user_uuid, device_id=device_uuid, block_id=bid,
                    state=BlockState(kind="x", content=f"event-{i}", completed=True),
                )
                await asyncio.sleep(0.05)
            # Wait past the cooldown so the trailing fire flushes.
            await asyncio.sleep(0.8)
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    assert len(fired) == 2, fired
    # First fire: the idle-path event.
    assert fired[0] == [("upload-file", "event-0")]
    # Second fire: the buffered events from the cooldown window.
    assert fired[1] == [("passage-reader", "event-1"), ("pdf-reader", "event-2")]


# ---- e2e through the running shell ----------------------------------------


def _device_headers(user_id: str, device_id: str | None = None) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Device-Id": device_id or str(uuid.uuid4()),
        "X-Device-Class": "desktop",
        "X-Device-Capabilities": json.dumps({"display": True, "speaker": True, "mic": False}),
    }


def _hold_dynamic_stream(shell_url: str, headers: dict[str, str], collect: list[dict],
                         open_seen: threading.Event, stop: threading.Event) -> threading.Thread:
    def reader():
        try:
            with httpx.Client(timeout=20.0, trust_env=False) as c:
                with c.stream("GET", f"{shell_url}/api/dynamic/stream", headers=headers) as r:
                    if r.status_code != 200:
                        return
                    for line in r.iter_lines():
                        if line.startswith("data: "):
                            try:
                                evt = json.loads(line[len("data: "):])
                            except json.JSONDecodeError:
                                continue
                            collect.append(evt)
                            if evt.get("type") == "open":
                                open_seen.set()
                        if stop.is_set():
                            return
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout):
            return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return t


def _wait_for(events: list[dict], pred, timeout: float = 8.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in events:
            if pred(e):
                return e
        time.sleep(0.05)
    return None


def test_completed_state_post_triggers_teacher_thinking(
    http: httpx.Client, shell_url: str, test_user_id: str
):
    """Posting a state with completed=true through the public HTTP path
    fires the teacher trigger end-to-end. We see two teacher-thinking
    SSE events (start + end) on the dynamic stream within the cooldown
    timeout."""
    headers = _device_headers(test_user_id)
    events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "SSE never opened"

        # First report sets the baseline (completed=false). The trigger
        # only fires on the false→true edge; this avoids racing against
        # any leftover state from earlier tests.
        r1 = http.post(
            "/api/dynamic/state/upload-file",
            headers={**headers, "Content-Type": "application/json"},
            json={"kind": "upload", "content": "uploading…", "completed": False},
        )
        assert r1.status_code == 200, r1.text

        r2 = http.post(
            "/api/dynamic/state/upload-file",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "kind": "upload",
                "content": "ready: paper.pdf",
                "completed": True,
                "extra": {"document_id": "doc-abc-123"},
            },
        )
        assert r2.status_code == 200, r2.text

        # The orchestrator now wakes for change events too, so a state
        # write fires both a change-bucket turn AND a completed-bucket turn.
        # Filter to the completion turn we actually care about here.
        start_evt = _wait_for(
            events,
            lambda e: (e.get("type") == "teacher-thinking"
                       and e.get("phase") == "start"
                       and e.get("trigger") == "block-completed"),
            timeout=10.0,
        )
        assert start_evt is not None, [e.get("type") for e in events]
        assert "upload-file" in (start_evt.get("summary") or "")

        end_evt = _wait_for(
            events,
            lambda e: (e.get("type") == "teacher-thinking"
                       and e.get("phase") == "end"
                       and e.get("trigger") == "block-completed"),
            timeout=15.0,
        )
        assert end_evt is not None, "no teacher-thinking end seen for block-completed"
        # tool_calls is a list (possibly empty for the fake provider)
        assert isinstance(end_evt.get("tool_calls"), list)
    finally:
        stop.set()
        t.join(timeout=2.0)
