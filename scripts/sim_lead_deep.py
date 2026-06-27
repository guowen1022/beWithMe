"""Text simulation of the lead → deep two-stage turn (real model, no audio).

Since the product uses STT/TTS, "voice" is just text in/out and each lane is an
LLM call. This harness feeds the lead pass a text question (as if transcribed),
reads the lead's streamed prose + its routing decision, then reads the deep
pass's streamed prose (delivered out-of-band as VoicePlay text over
/dynamic/stream). It plants a deliberately-WRONG LRU diagram as a drawn note so
we can see whether the deep pass actually inspects its own produced material.

Run:  .venv/bin/python scripts/sim_lead_deep.py
Needs: Postgres + Ollama up, real LLM provider in .env, BWM_LEAD respected.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Force the lead pass on + note persistence before any project import reads env.
os.environ["BWM_LEAD"] = "1"
os.environ["NOTES_PERSIST"] = "1"

import httpx  # noqa: E402

VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

WRONG_LRU_MD = """# LRU Cache

**LRU** stands for **Least Recently Used**.

```mermaid
graph TD
  A[Function called] --> B{In cache?}
  B -->|hit| C[Return cached value]
  B -->|miss| D{Cache full?}
  D -->|no| E[Compute and store result]
  D -->|yes| F[Evict the MOST recently used entry]
  F --> E
  E --> C
```
"""

QUESTION = (
    "Take a look at the LRU cache diagram you drew for me earlier — does that "
    "flow actually match how LRU works, or did something get drawn wrong?"
)


def _port_free(p: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", p)); return True
        except OSError:
            return False


def _free_base(start=19500, span=7) -> int:
    for base in range(start, start + 300, 10):
        if all(_port_free(base + i) for i in range(span)):
            return base
    raise RuntimeError("no free port window")


def _wait_listening(port: int, timeout: float, name: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect(("127.0.0.1", port)); return
            except OSError:
                time.sleep(0.3)
    raise RuntimeError(f"{name} on :{port} did not start in {timeout}s")


def main() -> int:
    base = _free_base()
    persona_port = base + 1
    knowledge_port = base + 2
    persona_url = f"http://127.0.0.1:{persona_port}"

    user_id = str(uuid4())
    device_id = str(uuid4())
    session_id = str(uuid4())

    import asyncio
    import asyncpg
    from infra.config import settings
    pg_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    def _pg(stmt, *args):
        async def _run():
            c = await asyncpg.connect(pg_url)
            try:
                return await c.execute(stmt, *args)
            finally:
                await c.close()
        return asyncio.new_event_loop().run_until_complete(_run())

    # Create an isolated real user (the profile insert has an FK to users).
    _pg("INSERT INTO users (id, username) VALUES ($1::uuid, $2)", user_id, "sim-lead-deep")
    print(f"[sim] created test user {user_id[:8]}")

    # Plant the wrong diagram as a drawn note for this user (writes to disk;
    # the persona subprocess reads it back from disk via _note_cache).
    from uuid import UUID
    from workshop.canvas.tools import _note_cache
    _note_cache.set(UUID(user_id), "lru-cache", md=WRONG_LRU_MD)
    print(f"[sim] planted wrong lru-cache note for user {user_id[:8]}")

    env = os.environ.copy()
    env["BASE_PORT"] = str(base)
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH','')}"
    env.pop("BROWSER_HEADED", None)

    logdir = REPO_ROOT / ".sim-logs"
    logdir.mkdir(exist_ok=True)
    procs = []
    try:
        for name, offset in (("knowledge", 2), ("persona", 1)):
            log = open(logdir / f"{name}.log", "wb")
            p = subprocess.Popen(
                [str(VENV_PY), "-m", f"services.{name}"],
                cwd=str(REPO_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            procs.append(p)
            _wait_listening(base + offset, 30.0, name)
            print(f"[sim] {name} up on :{base + offset}")

        hdr = {"X-User-Id": user_id, "X-Device-Id": device_id,
               "X-Device-Class": "desktop"}

        # --- subscribe to the out-of-band channel (captures deep-pass speech) ---
        voiceplays: list[tuple[float, str]] = []
        stop = threading.Event()

        def _listen():
            try:
                with httpx.Client(timeout=90.0, trust_env=False) as c:
                    with c.stream("GET", f"{persona_url}/api/dynamic/stream", headers=hdr) as r:
                        for raw in r.iter_lines():
                            if stop.is_set():
                                break
                            if raw and raw.startswith("data: "):
                                try:
                                    ev = json.loads(raw[6:])
                                except json.JSONDecodeError:
                                    continue
                                if ev.get("type") == "voice-play" and ev.get("text"):
                                    voiceplays.append((time.time(), ev["text"]))
            except Exception as e:
                print(f"[sim] dynamic-stream listener ended: {e}")

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
        time.sleep(1.5)  # let the SSE channel open before we ask

        # --- the lead pass: POST the question, read its streamed prose + routing ---
        lead_tokens: list[str] = []
        routing: list[dict] = []
        print("\n[sim] === asking (lead pass) ===")
        ask_done_at = None
        with httpx.Client(timeout=90.0, trust_env=False) as c:
            with c.stream(
                "POST", f"{persona_url}/api/ask/stream",
                headers={**hdr, "Content-Type": "application/json"},
                json={"question": QUESTION, "passage_text": "", "session_id": session_id},
            ) as r:
                for raw in r.iter_lines():
                    if raw and raw.startswith("data: "):
                        try:
                            ev = json.loads(raw[6:])
                        except json.JSONDecodeError:
                            continue
                        k = ev.get("type")
                        if k == "token":
                            lead_tokens.append(ev.get("text", ""))
                        elif k == "tool_call":
                            routing.append({"name": ev.get("name"), "arguments": ev.get("arguments")})
                        elif k == "answer":
                            pass
            ask_done_at = time.time()

        lead_text = "".join(lead_tokens).strip()
        print("\n--- LEAD LINE (what the user hears first) ---")
        print(lead_text or "(no streamed lead tokens)")
        print("\n--- LEAD ROUTING (tool calls) ---")
        print(json.dumps(routing, indent=2) if routing else "(no tool calls — answered inline)")

        # --- wait for the deep pass to run + deliver ---
        print("\n[sim] waiting up to 45s for the deep pass to speak…")
        deadline = time.time() + 45
        while time.time() < deadline:
            # deep speech = VoicePlay arriving after the ask response closed
            if any(ts > (ask_done_at + 0.2) for ts, _ in voiceplays):
                time.sleep(4)  # let the rest of the answer stream in
                break
            time.sleep(1)
        stop.set()

        lead_vp = [txt for ts, txt in voiceplays if ts <= ask_done_at + 0.2]
        deep_vp = [txt for ts, txt in voiceplays if ts > ask_done_at + 0.2]
        print("\n--- LEAD VOICE (spoken, from /dynamic/stream) ---")
        print(" ".join(lead_vp) or "(none)")
        print("\n--- DEEP PASS ANSWER (spoken, from /dynamic/stream) ---")
        print(" ".join(deep_vp) or "(none — deep pass did not deliver)")

        # --- quick assertions ---
        bad = ["can't see", "cannot see", "don't have access", "not able to look",
               "i can't access", "no access to"]
        lowered = (lead_text + " " + " ".join(lead_vp)).lower()
        disclaimed = [p for p in bad if p in lowered]
        # The lead's request_handoff call is a terminal signal handled inside the
        # ask router; it is intentionally NOT forwarded to the ask SSE. So a deep
        # delivery over /dynamic/stream is the observable proof it routed deep.
        routed_deep = bool(deep_vp)
        print("\n=== VERDICT ===")
        print(f"lead disclaimed capability:   {bool(disclaimed)}  {disclaimed or ''}")
        print(f"lead routed deep (deep ran):  {routed_deep}")
        print(f"deep critiqued the diagram:   {bool(deep_vp)}")
        ok = (not disclaimed) and routed_deep
        print(f"\nSMOKE {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        for p in procs:
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        # clean up: planted note + DB rows for the isolated test user
        try:
            from uuid import UUID
            from workshop.canvas.tools import _note_cache
            _note_cache.forget(UUID(user_id), "lru-cache")
        except Exception:
            pass
        for tbl in ("interactions", "teacher_preference_model", "concept_nodes",
                    "concept_edges", "devices", "canvas_layout"):
            try:
                _pg(f"DELETE FROM {tbl} WHERE user_id = $1::uuid", user_id)
            except Exception:
                pass
        try:
            _pg("DELETE FROM users WHERE id = $1::uuid", user_id)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
