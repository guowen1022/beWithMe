"""End-to-end ask flow — real DB, real persona sidecar, fake LLM.

Exercises the full write path:
  shell auth → persona sidecar → assemble_context (real DB reads) →
  fake LLM tokens → SSE stream → Interaction row written → brain_builder
  fired in background.

Catches issues like the FK-resolution bug that the lighter `test_e2e.py`
suite missed (it never authenticated to /api/ask, so the persona DB write
path was never exercised).
"""
from __future__ import annotations

import json
import os
import time
import uuid

import asyncpg
import httpx
import pytest


def _asyncpg_url() -> str:
    """Strip SQLAlchemy +asyncpg dialect prefix for raw asyncpg."""
    # The test sidecars share .env with the dev backend, so DATABASE_URL is
    # the same Postgres the test asks against.
    from infra.config import settings
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _count_interactions(user_id: str) -> int:
    conn = await asyncpg.connect(_asyncpg_url())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM interactions WHERE user_id = $1::uuid", user_id
        )
    finally:
        await conn.close()


def _consume_sse(resp: httpx.Response, timeout: float = 30.0) -> list[dict]:
    """Read SSE events until the stream closes or `timeout` elapses."""
    deadline = time.time() + timeout
    events: list[dict] = []
    for raw in resp.iter_lines():
        if time.time() > deadline:
            break
        if not raw:
            continue
        if raw.startswith("data: "):
            try:
                events.append(json.loads(raw[len("data: "):]))
            except json.JSONDecodeError:
                continue
    return events


def test_ask_stream_writes_interaction(http: httpx.Client, auth: dict, test_user_id: str):
    """Full ask flow: SSE stream → Interaction row in DB."""
    body = {
        "question": "What is mitochondrial DNA?",
        "passage_text": "",
        "session_id": str(uuid.uuid4()),
    }

    # Establish baseline: no interactions for this user yet.
    import asyncio
    before = asyncio.run(_count_interactions(test_user_id))

    with http.stream(
        "POST", "/api/ask/stream",
        headers={**auth, "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _consume_sse(resp, timeout=20.0)

    # SSE shape — fake LLM emits a TITLE line + body + CONCEPTS:.
    kinds = [e.get("type") for e in events]
    assert "title" in kinds, f"no title event in stream: {kinds}"
    assert "token" in kinds, f"no token events in stream: {kinds}"
    assert "answer" in kinds, f"no final answer event in stream: {kinds}"
    assert "interaction" in kinds, f"no interaction-id event (write path didn't run): {kinds}"

    # Title from the fake provider.
    title_evt = next(e for e in events if e["type"] == "title")
    assert "Fake test answer" in title_evt["title"]

    # Verify a row was written to the real DB.
    after = asyncio.run(_count_interactions(test_user_id))
    assert after == before + 1, f"expected 1 new interaction; before={before} after={after}"


def test_ask_non_streaming_writes_interaction(http: httpx.Client, auth: dict, test_user_id: str):
    """Same write path via the non-streaming endpoint."""
    import asyncio
    before = asyncio.run(_count_interactions(test_user_id))

    body = {
        "question": "What's the role of ATP synthase?",
        "passage_text": "",
        "session_id": str(uuid.uuid4()),
    }
    resp = http.post(
        "/api/ask",
        headers={**auth, "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "interaction_id" in payload
    assert "answer" in payload
    assert "Fake test answer" in payload["answer"]

    # Real DB row written.
    after = asyncio.run(_count_interactions(test_user_id))
    assert after == before + 1
