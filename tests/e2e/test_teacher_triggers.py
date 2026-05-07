"""E2E for the two-lane teacher-trigger orchestrator.

Lane A — user-facing, single task, 500 ms debounce, preemptable.
Lane B — background pool, fire-and-forget, multiple parallel tasks per user.

Covers:
  * cache edge-detect: completed:false → true emits BlockCompletedEvent;
    re-true→true does not refire (unchanged from the bucket era).
  * lane A: debounce + dedupe; preemption on new user_speech; drops
    teacher-self voice events.
  * lane B: fires immediately on BlockCompletedEvent; runs in parallel
    with lane A; appends notices that lane A drains next turn.
  * lane A is dropped for `BlockChangeEvent` (no "change" reflect).
  * end-to-end: a completed-state POST fires a `lane-b` teacher-thinking
    SSE event on the dynamic stream.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import httpx
import pytest


# ---- in-process: cache edge detection (unchanged) -------------------------


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
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="uploading", completed=False),
            )
            await asyncio.sleep(0.05)
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready", completed=True),
            )
            await asyncio.sleep(0.05)
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


# ---- in-process: lane orchestrator ----------------------------------------


def _drive_speech(perc_cache, user_uuid, text: str, target: str = "teacher"):
    """Helper: synchronous call to record one user_speech utterance."""
    perc_cache.record_user_speech(
        user_id=user_uuid,
        text=text,
        language="en",
        target_persona=target,
    )


def test_lane_a_debounces_and_dedupes(monkeypatch, test_user_id: str):
    """Three user_speech utterances inside the 500 ms debounce window
    fire exactly one Lane A turn after the debounce expires. Identical
    text gets deduped to one event in the events list."""
    from infra.perception import cache as perc_cache
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()
    # Tighten the debounce so the test is fast.
    monkeypatch.setattr(triggers, "LANE_A_DEBOUNCE_S", 0.15)

    user_uuid = uuid.UUID(test_user_id)
    fired: list = []

    async def fake_conversation(user_id, events):
        fired.append([(e.event_type, e.content) for e in events])

    monkeypatch.setattr(triggers, "_execute_conversation", fake_conversation)

    async def drive():
        triggers.install()
        try:
            _drive_speech(perc_cache, user_uuid, "hello")
            await asyncio.sleep(0.05)
            _drive_speech(perc_cache, user_uuid, "hello")
            await asyncio.sleep(0.05)
            _drive_speech(perc_cache, user_uuid, "hello")
            # Wait past the debounce + a buffer for the task to run.
            await asyncio.sleep(0.4)
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    assert len(fired) == 1, fired
    # Three identical "hello"s → deduped to one event.
    assert fired[0] == [("user_speech", "hello")], fired


def test_lane_a_preempts_running_turn(monkeypatch, test_user_id: str):
    """A new user_speech arriving while Lane A is mid-LLM cancels the
    running task and re-fires after the debounce with both events."""
    from infra.perception import cache as perc_cache
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()
    monkeypatch.setattr(triggers, "LANE_A_DEBOUNCE_S", 0.05)

    user_uuid = uuid.UUID(test_user_id)
    fired: list = []
    cancellations: list = []

    async def fake_conversation(user_id, events):
        # Simulate an LLM call. The first invocation should be cancelled
        # by the second user_speech; the second invocation should run
        # to completion with the merged events list.
        try:
            await asyncio.sleep(0.3)
            fired.append([(e.event_type, e.content) for e in events])
        except asyncio.CancelledError:
            cancellations.append([(e.event_type, e.content) for e in events])
            raise

    monkeypatch.setattr(triggers, "_execute_conversation", fake_conversation)

    async def drive():
        triggers.install()
        try:
            _drive_speech(perc_cache, user_uuid, "first")
            await asyncio.sleep(0.15)  # debounce (0.05) + first task running
            _drive_speech(perc_cache, user_uuid, "second")
            await asyncio.sleep(0.6)  # let preempted task die + new task finish
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    # The first task should have been cancelled, the second task ran with
    # the merged events list.
    assert len(cancellations) == 1, cancellations
    assert ("user_speech", "first") in cancellations[0]
    assert len(fired) == 1, fired
    fired_contents = [c for _, c in fired[0]]
    assert "first" in fired_contents and "second" in fired_contents, fired


def test_lane_a_drops_teacher_self_voice(monkeypatch, test_user_id: str):
    """A VoiceEvent with source='teacher' is dropped at classify time.
    A non-teacher voice event still routes to Lane A."""
    from infra.perception import cache as perc_cache
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()
    monkeypatch.setattr(triggers, "LANE_A_DEBOUNCE_S", 0.05)

    user_uuid = uuid.UUID(test_user_id)
    fired: list = []

    async def fake_conversation(user_id, events):
        fired.append([(e.event_type, e.content) for e in events])

    monkeypatch.setattr(triggers, "_execute_conversation", fake_conversation)

    async def drive():
        triggers.install()
        try:
            # Teacher speaks — should be dropped.
            perc_cache.record_voice(
                user_id=user_uuid,
                text="hi from the teacher",
                voice="af_heart",
                source="teacher",
            )
            await asyncio.sleep(0.3)
            assert fired == [], "teacher self-voice leaked into Lane A"

            # External voice — should fire Lane A.
            perc_cache.record_voice(
                user_id=user_uuid,
                text="external speaker",
                voice=None,
                source="external",
            )
            await asyncio.sleep(0.3)
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    assert len(fired) == 1, fired
    assert fired[0] == [("voice", "external speaker")]


def test_lane_b_fires_immediately_on_block_completed(monkeypatch, test_user_id: str):
    """BlockCompletedEvent spawns a Lane B task immediately, no debounce."""
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockState
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()

    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()
    fired: list = []

    async def fake_background(user_id, summary):
        fired.append((summary.event_type, summary.block_id))

    monkeypatch.setattr(triggers, "_execute_background", fake_background)

    async def drive():
        triggers.install()
        try:
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready", completed=True),
            )
            await asyncio.sleep(0.15)
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    assert fired == [("completed", "upload-file")], fired


def test_lane_b_runs_in_parallel_with_lane_a(monkeypatch, test_user_id: str):
    """A BlockCompletedEvent that fires while Lane A is mid-turn does NOT
    wait — both run as concurrent tasks."""
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockState
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()
    monkeypatch.setattr(triggers, "LANE_A_DEBOUNCE_S", 0.05)

    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()
    timeline: list[tuple[str, float]] = []
    t0 = None

    async def fake_conversation(user_id, events):
        timeline.append(("a-start", asyncio.get_event_loop().time() - t0))
        await asyncio.sleep(0.5)
        timeline.append(("a-end", asyncio.get_event_loop().time() - t0))

    async def fake_background(user_id, summary):
        timeline.append(("b-start", asyncio.get_event_loop().time() - t0))
        await asyncio.sleep(0.2)
        timeline.append(("b-end", asyncio.get_event_loop().time() - t0))

    monkeypatch.setattr(triggers, "_execute_conversation", fake_conversation)
    monkeypatch.setattr(triggers, "_execute_background", fake_background)

    async def drive():
        nonlocal t0
        t0 = asyncio.get_event_loop().time()
        triggers.install()
        try:
            _drive_speech(perc_cache, user_uuid, "hello")
            await asyncio.sleep(0.15)  # Lane A is now running
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="upload-file",
                state=BlockState(kind="upload", content="ready", completed=True),
            )
            await asyncio.sleep(0.8)  # both should finish
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    # Lane B should start before Lane A ends — proves parallel.
    a_start = next(t for k, t in timeline if k == "a-start")
    a_end = next(t for k, t in timeline if k == "a-end")
    b_start = next(t for k, t in timeline if k == "b-start")
    b_end = next(t for k, t in timeline if k == "b-end")
    assert b_start < a_end, f"Lane B did not start while Lane A was running: {timeline}"
    assert b_end < a_end, f"Lane B did not finish before Lane A: {timeline}"


def test_lane_b_appends_notice_lane_a_drains_it(monkeypatch, test_user_id: str):
    """Lane B's notice is appended to the per-user deque; Lane A's
    `assemble_reflect` drains it on the next turn."""
    from persona.teacher import notices

    notices._reset_for_tests()
    user_uuid = uuid.UUID(test_user_id)
    notices.append(user_uuid, "background: mounted pdf_reader as paper-1")

    # peek shouldn't consume.
    assert notices.peek(user_uuid) == ["background: mounted pdf_reader as paper-1"]
    assert notices.peek(user_uuid) == ["background: mounted pdf_reader as paper-1"]

    # drain consumes.
    drained = notices.drain(user_uuid)
    assert drained == ["background: mounted pdf_reader as paper-1"]
    assert notices.peek(user_uuid) == []


def test_block_change_event_is_dropped(monkeypatch, test_user_id: str):
    """An ambient BlockChangeEvent (state recorded with completed=False)
    does NOT fire any orchestrator task — neither lane."""
    from infra.perception import cache as perc_cache
    from infra.perception.contracts import BlockState
    from persona.teacher import triggers, notices

    perc_cache._reset_for_tests()
    triggers._reset_for_tests()
    notices._reset_for_tests()
    monkeypatch.setattr(triggers, "LANE_A_DEBOUNCE_S", 0.05)

    user_uuid = uuid.UUID(test_user_id)
    device_uuid = uuid.uuid4()
    a_fired: list = []
    b_fired: list = []

    async def fake_conversation(user_id, events):
        a_fired.append(events)

    async def fake_background(user_id, summary):
        b_fired.append(summary)

    monkeypatch.setattr(triggers, "_execute_conversation", fake_conversation)
    monkeypatch.setattr(triggers, "_execute_background", fake_background)

    async def drive():
        triggers.install()
        try:
            # An ambient state report (page change). completed=False so
            # only a BlockChangeEvent fires.
            perc_cache.record_block_state(
                user_id=user_uuid, device_id=device_uuid, block_id="pdf-reader",
                state=BlockState(kind="pdf", content="page 7", completed=False),
            )
            await asyncio.sleep(0.3)
        finally:
            triggers.uninstall()

    asyncio.run(drive())

    assert a_fired == [], "Lane A fired on ambient block change"
    assert b_fired == [], "Lane B fired on ambient block change"


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


def test_completed_state_post_triggers_lane_b(
    http: httpx.Client, shell_url: str, test_user_id: str
):
    """Posting a state with completed=true through the public HTTP path
    fires Lane B end-to-end. We see two teacher-thinking SSE events
    (start + end) on the dynamic stream with `trigger="lane-b"`."""
    headers = _device_headers(test_user_id)
    events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "SSE never opened"

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

        start_evt = _wait_for(
            events,
            lambda e: (e.get("type") == "teacher-thinking"
                       and e.get("phase") == "start"
                       and e.get("trigger") == "lane-b"),
            timeout=10.0,
        )
        assert start_evt is not None, [e.get("type") for e in events]
        assert "upload-file" in (start_evt.get("summary") or "")

        end_evt = _wait_for(
            events,
            lambda e: (e.get("type") == "teacher-thinking"
                       and e.get("phase") == "end"
                       and e.get("trigger") == "lane-b"),
            timeout=20.0,
        )
        assert end_evt is not None, "no teacher-thinking end seen for lane-b"
        assert isinstance(end_evt.get("tool_calls"), list)
    finally:
        stop.set()
        t.join(timeout=2.0)
