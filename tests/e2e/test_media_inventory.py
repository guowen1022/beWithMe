"""E2E for the device registry + list_media tool (P1).

Drives the running shell from outside the process. Every request goes
through the proxy + auth gate + routing table, just like the frontend
does. Uses LLM_PROVIDER=fake (set by conftest) so no LLM is involved.
"""
from __future__ import annotations

import json
import threading
import uuid

import httpx
import pytest


def _device_headers(user_id: str, device_id: str | None = None,
                    device_class: str = "desktop",
                    capabilities: dict | None = None) -> dict[str, str]:
    caps = capabilities or {"display": True, "speaker": True, "mic": False}
    return {
        "X-User-Id": user_id,
        "X-Device-Id": device_id or str(uuid.uuid4()),
        "X-Device-Class": device_class,
        "X-Device-Capabilities": json.dumps(caps),
    }


def _open_stream_and_close(http_url: str, headers: dict[str, str]) -> None:
    """Open the SSE stream, read the initial 'open' event, then close.

    The server registers the device on connect; this is enough to flip
    `online=true` in the registry while the stream is held, and to leave
    a `devices` row behind once we close.
    """
    with httpx.Client(timeout=10.0, trust_env=False) as c:
        with c.stream("GET", f"{http_url}/api/dynamic/stream", headers=headers) as r:
            assert r.status_code == 200
            # Read until we see the first SSE message, then drop the connection.
            for line in r.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: "):])
                    assert payload["type"] == "open"
                    assert payload["device_id"] == headers["X-Device-Id"]
                    return


def test_media_unknown_user_is_401(http: httpx.Client):
    """Without a valid X-User-Id, /api/dynamic/media is gated at the shell."""
    resp = http.get("/api/dynamic/media", headers={"X-User-Id": "not-a-uuid"})
    assert resp.status_code == 401


def test_media_empty_inventory_for_fresh_user(http: httpx.Client, auth: dict[str, str]):
    """A user with no connected devices yet returns empty canvases + voices."""
    resp = http.get("/api/dynamic/media", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["canvases"] == []
    assert body["voices"] == []


def test_device_registers_on_sse_connect(
    http: httpx.Client, shell_url: str, test_user_id: str
):
    """Connecting to /api/dynamic/stream registers the device.

    After the stream closes, `/api/dynamic/media` should still list the
    device (last-seen) but with `online=false`.
    """
    device_id = str(uuid.uuid4())
    headers = _device_headers(test_user_id, device_id=device_id)
    _open_stream_and_close(shell_url, headers)

    resp = http.get("/api/dynamic/media", headers=headers)
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    canvases = inv["canvases"]
    assert any(c["device_id"] == device_id for c in canvases), inv
    # speaker:true in the test caps → voice entry too.
    assert any(v["device_id"] == device_id for v in inv["voices"]), inv

    # Stream is closed now — device should be offline but still listed.
    target = next(c for c in canvases if c["device_id"] == device_id)
    assert target["online"] is False
    assert target["device_class"] == "desktop"
    # No mounts yet.
    assert target["blocks"] == []


def test_two_devices_show_as_two_canvases(
    http: httpx.Client, shell_url: str, test_user_id: str
):
    """Same user, two distinct device_ids → two canvases in list_media."""
    laptop_headers = _device_headers(
        test_user_id, device_class="desktop",
        capabilities={"display": True, "speaker": True, "mic": False},
    )
    phone_headers = _device_headers(
        test_user_id, device_class="phone",
        capabilities={"display": True, "speaker": True, "mic": True},
    )
    _open_stream_and_close(shell_url, laptop_headers)
    _open_stream_and_close(shell_url, phone_headers)

    resp = http.get("/api/dynamic/media", headers=laptop_headers)
    assert resp.status_code == 200, resp.text
    canvases = resp.json()["canvases"]
    ids = {c["device_id"] for c in canvases}
    assert laptop_headers["X-Device-Id"] in ids
    assert phone_headers["X-Device-Id"] in ids
    classes = {c["device_id"]: c["device_class"] for c in canvases}
    assert classes[laptop_headers["X-Device-Id"]] == "desktop"
    assert classes[phone_headers["X-Device-Id"]] == "phone"


def test_canvas_layout_populated_by_block_mount(
    http: httpx.Client, shell_url: str, test_user_id: str
):
    """Mounting the hello block via /block hello writes a canvas_layout row,
    visible as a block on the device's canvas via list_media.

    Strategy: hold the SSE stream open in a worker thread (so the device
    is registered as online when /block fires), trigger the mount on the
    main thread, then poll /api/dynamic/media until the layout row shows
    up. The reader thread tolerates the eventual stream close — uvicorn
    can disconnect mid-iteration and that's not a test failure.
    """
    import time as _t

    device_id = str(uuid.uuid4())
    headers = _device_headers(test_user_id, device_id=device_id)

    open_seen = threading.Event()
    stop_reader = threading.Event()

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
                            if evt.get("type") == "open":
                                open_seen.set()
                        if stop_reader.is_set():
                            return
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout):
            # Server-side stream close on shutdown is expected.
            return

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    assert open_seen.wait(timeout=5.0), "SSE never opened"

    ask_resp = httpx.post(
        f"{shell_url}/api/ask/stream",
        headers=headers,
        json={"question": "/block hello"},
        timeout=30.0,
    )
    assert ask_resp.status_code == 200, ask_resp.text
    _ = ask_resp.text  # drain the synthetic SSE body

    # Poll list_media until we see the mount land — the engineer turn +
    # canvas_layout write happens after the ask response returns its body.
    deadline = _t.time() + 8.0
    canvas = None
    while _t.time() < deadline:
        inv_resp = http.get("/api/dynamic/media", headers=headers)
        assert inv_resp.status_code == 200, inv_resp.text
        canvases = inv_resp.json()["canvases"]
        canvas = next((c for c in canvases if c["device_id"] == device_id), None)
        if canvas and any(b["id"] == "hello" for b in canvas["blocks"]):
            break
        _t.sleep(0.2)

    stop_reader.set()
    t.join(timeout=2.0)

    assert canvas is not None, "device not in canvases after mount"
    block_ids = [b["id"] for b in canvas["blocks"]]
    assert "hello" in block_ids, f"hello block not found on canvas: {canvas}"
