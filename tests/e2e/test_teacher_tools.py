"""E2E for the teacher tool loop (P2).

Drives the running shell with messages whose substrings trigger the fake
provider's scripted tool calls. Verifies:

  * `/ask/stream` produces a `block-action` SSE event on the dynamic
    channel when the teacher's `block_action` tool fires.
  * `/ask/stream` produces a `ui-update mount` event when the teacher's
    `request_new_block` tool fires.
  * The final SSE answer event still lands (tool loop returns to the
    answer turn after tools execute).
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import httpx
import pytest


def _device_headers(user_id: str, device_id: str | None = None) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Device-Id": device_id or str(uuid.uuid4()),
        "X-Device-Class": "desktop",
        "X-Device-Capabilities": json.dumps({"display": True, "speaker": True, "mic": False}),
    }


def _hold_dynamic_stream(shell_url: str, headers: dict[str, str], collect: list[dict],
                         open_seen: threading.Event, stop: threading.Event) -> threading.Thread:
    """Run an SSE reader in a background thread; collect every parsed event."""
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
        time.sleep(0.1)
    return None


def test_teacher_tool_block_action_emits_sse_event(
    shell_url: str, test_user_id: str
):
    """A question containing 'block_action highlight block=hello' triggers
    the fake provider's scripted block_action call → SSE 'block-action'
    fans out on the dynamic stream → final answer event lands too."""
    headers = _device_headers(test_user_id)
    dynamic_events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, dynamic_events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "dynamic SSE never opened"

        ask_resp = httpx.post(
            f"{shell_url}/api/ask/stream",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "question": "please block_action highlight block=hello",
                "session_id": str(uuid.uuid4()),
            },
            timeout=30.0,
        )
        assert ask_resp.status_code == 200
        ask_text = ask_resp.text  # drain

        # The fake provider scripts a tool call on the matching keyword.
        # The loop should execute the block_action tool, which fans out a
        # BlockAction SSE event on /api/dynamic/stream.
        evt = _wait_for(
            dynamic_events,
            lambda e: e.get("type") == "block-action"
            and e.get("block_id") == "hello"
            and e.get("action") == "highlight",
            timeout=8.0,
        )
        assert evt is not None, f"no block-action event seen; got types={[e.get('type') for e in dynamic_events]}"

        # And the ask stream should still finish with an `answer` event
        # (the fake provider always answers on the follow-up turn).
        ask_events = []
        for raw in ask_text.splitlines():
            if raw.startswith("data: "):
                try:
                    ask_events.append(json.loads(raw[len("data: "):]))
                except json.JSONDecodeError:
                    continue
        kinds = [e.get("type") for e in ask_events]
        assert "answer" in kinds, f"ask stream never produced an answer event: {kinds}"
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_teacher_tool_request_new_block_mounts_block(
    shell_url: str, test_user_id: str
):
    """A question containing 'request_new_block' triggers the fake
    provider's scripted call → engineer mounts the hello block → SSE
    'ui-update mount' fans out."""
    headers = _device_headers(test_user_id)
    dynamic_events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, dynamic_events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "dynamic SSE never opened"

        ask_resp = httpx.post(
            f"{shell_url}/api/ask/stream",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "question": "please request_new_block for a hello widget",
                "session_id": str(uuid.uuid4()),
            },
            timeout=30.0,
        )
        assert ask_resp.status_code == 200
        _ = ask_resp.text

        evt = _wait_for(
            dynamic_events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "mount"
            and (e.get("block") or {}).get("id") == "hello",
            timeout=8.0,
        )
        assert evt is not None, f"no ui-update mount seen; got types={[e.get('type') for e in dynamic_events]}"
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_teacher_tool_speak_emits_voice_play_event(
    shell_url: str, test_user_id: str
):
    """A question containing 'speak' triggers the fake provider's scripted
    speak call → SSE 'voice-play' event lands on the dynamic stream with
    voice prefs filled in from the user's UserPreferences row (defaults
    apply when no row exists)."""
    headers = _device_headers(test_user_id)
    dynamic_events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, dynamic_events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "dynamic SSE never opened"

        ask_resp = httpx.post(
            f"{shell_url}/api/ask/stream",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "question": "please speak something to me",
                "session_id": str(uuid.uuid4()),
            },
            timeout=30.0,
        )
        assert ask_resp.status_code == 200
        _ = ask_resp.text

        evt = _wait_for(
            dynamic_events,
            lambda e: e.get("type") == "voice-play"
            and "fake provider" in (e.get("text") or ""),
            timeout=8.0,
        )
        assert evt is not None, (
            f"no voice-play event seen; got types={[e.get('type') for e in dynamic_events]}"
        )
        # Defaults from UserPreferences (kokoro-matched) should be filled in
        # by the speak tool even though the LLM only supplied `text`.
        assert evt.get("voice") == "af_heart"
        assert evt.get("lang") == "en-us"
        assert evt.get("speed") == 1.0
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_teacher_tool_point_arrow_mounts_overlay_and_publishes(
    shell_url: str, test_user_id: str
):
    """A 'point_arrow' question fires the fake provider's scripted call,
    which (a) ensures the arrow-overlay block is mounted on the canvas
    (ui-update), and (b) publishes from/to via the bus (block-data on
    topic 'arrow')."""
    headers = _device_headers(test_user_id)
    dynamic_events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, dynamic_events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "dynamic SSE never opened"

        ask_resp = httpx.post(
            f"{shell_url}/api/ask/stream",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "question": "please point_arrow from hello to world",
                "session_id": str(uuid.uuid4()),
            },
            timeout=30.0,
        )
        assert ask_resp.status_code == 200
        _ = ask_resp.text

        mount_evt = _wait_for(
            dynamic_events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "mount"
            and (e.get("block") or {}).get("id") == "arrow-overlay",
            timeout=8.0,
        )
        assert mount_evt is not None, (
            f"no arrow-overlay mount; got types={[e.get('type') for e in dynamic_events]}"
        )

        pub_evt = _wait_for(
            dynamic_events,
            lambda e: e.get("type") == "block-data"
            and e.get("block_id") == "arrow-overlay"
            and e.get("topic") == "arrow"
            and (e.get("value") or {}).get("from") == "hello"
            and (e.get("value") or {}).get("to") == "world",
            timeout=8.0,
        )
        assert pub_evt is not None, "no arrow block-data publish seen"
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_teacher_no_tool_keyword_just_answers(
    http: httpx.Client, auth: dict, test_user_id: str
):
    """When no tool name appears in the message, the teacher just answers —
    no tool calls fire. This confirms the loop's no-op exit path works."""
    body = {
        "question": "what is mitochondrial DNA?",
        "session_id": str(uuid.uuid4()),
    }
    with http.stream(
        "POST", "/api/ask/stream",
        headers={**auth, "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    ) as resp:
        assert resp.status_code == 200
        events = []
        for raw in resp.iter_lines():
            if raw.startswith("data: "):
                try:
                    events.append(json.loads(raw[len("data: "):]))
                except json.JSONDecodeError:
                    continue
    kinds = [e.get("type") for e in events]
    assert "answer" in kinds, f"no answer event: {kinds}"
    answer_evt = next(e for e in events if e["type"] == "answer")
    # Fake provider's title is "Fake test answer for e2e"; body has the
    # boilerplate disclaimer. Either is enough to confirm we got the
    # canned answer back.
    assert "Fake test answer" in (answer_evt.get("title") or "")
    assert "fake LLM provider" in (answer_evt.get("answer") or "")
