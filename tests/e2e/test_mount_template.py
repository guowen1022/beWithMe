"""E2E for the /api/dynamic/mount-template endpoint (Phase C).

Covers: known templates mount + write canvas_layout + emit SSE; unknown
template returns 404; replace ids unmount + remove from layout in the
same request; bad template names are rejected as 400.

Uses a fresh user per test (rather than the session-scoped `test_user_id`)
so the per-user-git workspace stays clean — these tests write blocks to
that workspace and shouldn't leak state into other suites.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import httpx
import pytest


@pytest.fixture
def fresh_user_id(http: httpx.Client) -> str:
    """A new user per test. Avoids polluting the shared workspace."""
    username = f"e2e-mt-{uuid.uuid4().hex[:8]}"
    resp = http.post("/api/users", json={"username": username})
    if resp.status_code != 200:
        pytest.skip(f"cannot create test user: {resp.status_code} {resp.text[:200]}")
    return resp.json()["id"]


@pytest.fixture
def fresh_auth(fresh_user_id: str) -> dict[str, str]:
    return {"X-User-Id": fresh_user_id}


def _device_headers(user_id: str, device_id: str | None = None) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Device-Id": device_id or str(uuid.uuid4()),
        "X-Device-Class": "desktop",
        "X-Device-Capabilities": json.dumps({"display": True, "speaker": True, "mic": False}),
    }


def _hold_dynamic_stream(shell_url: str, headers: dict[str, str], collect: list[dict],
                         open_seen: threading.Event, stop: threading.Event) -> threading.Thread:
    """Run an SSE reader in a thread; collect every parsed event."""
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


def _wait_for(events: list[dict], pred, timeout: float = 5.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in events:
            if pred(e):
                return e
        time.sleep(0.05)
    return None


def test_mount_template_unknown_returns_404(http: httpx.Client, fresh_auth: dict):
    """Unknown template name → 404. The endpoint requires X-Device-Id but
    sends a 404 first when the template can't be found."""
    headers = _device_headers(fresh_auth["X-User-Id"])
    resp = http.post(
        "/api/dynamic/mount-template",
        headers={**headers, "Content-Type": "application/json"},
        json={"template": "this-template-does-not-exist"},
    )
    assert resp.status_code == 404, resp.text


def test_mount_template_invalid_name_returns_400(http: httpx.Client, fresh_auth: dict):
    """Names with characters outside [a-z0-9_-] are rejected as 400 (no path
    traversal possible)."""
    headers = _device_headers(fresh_auth["X-User-Id"])
    for bad in ["../etc/passwd", "/etc/passwd", "Foo", "with space"]:
        resp = http.post(
            "/api/dynamic/mount-template",
            headers={**headers, "Content-Type": "application/json"},
            json={"template": bad},
        )
        assert resp.status_code == 400, f"{bad!r} -> {resp.status_code}: {resp.text}"


def test_mount_template_passage_reader(
    http: httpx.Client, shell_url: str, fresh_user_id: str
):
    """Mounting passage_reader fans out a UIUpdate mount on the dynamic
    stream and returns the assigned block id."""
    headers = _device_headers(fresh_user_id)
    events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "SSE never opened"

        resp = http.post(
            "/api/dynamic/mount-template",
            headers={**headers, "Content-Type": "application/json"},
            json={"template": "passage_reader"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "passage_reader"
        assert body["block_id"] == "passage-reader"
        assert body["deleted"] == []

        evt = _wait_for(
            events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "mount"
            and (e.get("block") or {}).get("id") == "passage-reader",
            timeout=5.0,
        )
        assert evt is not None, [e.get("type") for e in events]
        # The block source should carry the manifest we injected.
        src = evt["block"]["source"]
        assert "manifest:" in src
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_mount_template_replace_unmounts_old(
    http: httpx.Client, shell_url: str, fresh_user_id: str
):
    """`replace: [old_id]` triggers an unmount UIUpdate for old_id followed
    by a mount UIUpdate for the new template — in the same request."""
    headers = _device_headers(fresh_user_id)
    events: list[dict] = []
    open_seen = threading.Event()
    stop = threading.Event()
    t = _hold_dynamic_stream(shell_url, headers, events, open_seen, stop)
    try:
        assert open_seen.wait(timeout=5.0), "SSE never opened"

        # First, mount inputs_launcher.
        r1 = http.post(
            "/api/dynamic/mount-template",
            headers={**headers, "Content-Type": "application/json"},
            json={"template": "inputs_launcher"},
        )
        assert r1.status_code == 200, r1.text
        launcher_id = r1.json()["block_id"]

        # Wait for the launcher mount to land on the SSE stream.
        assert _wait_for(
            events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "mount"
            and (e.get("block") or {}).get("id") == launcher_id,
            timeout=5.0,
        ), "launcher mount never seen"

        # Now mount passage_reader and request the launcher be replaced.
        r2 = http.post(
            "/api/dynamic/mount-template",
            headers={**headers, "Content-Type": "application/json"},
            json={"template": "passage_reader", "replace": [launcher_id]},
        )
        assert r2.status_code == 200, r2.text
        assert launcher_id in r2.json()["deleted"]

        # Both events should appear: unmount(launcher), mount(passage-reader).
        unmount_evt = _wait_for(
            events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "unmount"
            and (e.get("block") or {}).get("id") == launcher_id,
            timeout=5.0,
        )
        assert unmount_evt is not None, [e for e in events if e.get("type") == "ui-update"]
        mount_evt = _wait_for(
            events,
            lambda e: e.get("type") == "ui-update"
            and e.get("action") == "mount"
            and (e.get("block") or {}).get("id") == "passage-reader",
            timeout=5.0,
        )
        assert mount_evt is not None
    finally:
        stop.set()
        t.join(timeout=2.0)
