"""E2E fixture: spin up all 6 sidecars on a fresh BASE_PORT, tear them down.

Tests in this directory talk to the shell from outside the process — same way
the frontend does. No app objects, no TestClient, no dependency overrides.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# Each (service, offset) pair must match infra.topology.SERVICE_OFFSETS.
SIDECARS = [
    ("knowledge", 2),
    ("persona", 1),
    ("transcribe", 3),
    ("speak", 4),
    ("browser", 5),
    ("maestro", 6),
    ("shell", 0),  # shell last so upstreams are ready before it accepts traffic
]


def _free_base_port(start: int = 18000, span: int = 7) -> int:
    """Find a base port where ports [start, start+span) are all free."""
    for base in range(start, start + 200, 10):
        if all(_port_is_free(base + i) for i in range(span)):
            return base
    raise RuntimeError(f"No free port window found near {start}")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


DEFAULT_USER_UUID_STR = "00000000-0000-0000-0000-000000000000"


def _wipe_dedicated_user_state() -> None:
    """Reset rows for the dedicated test user so each session starts clean.

    The e2e suite reuses DEFAULT_USER_ID (and a small set of stable device
    UUIDs) across runs to avoid filling the `devices` table with one ghost
    row per test invocation. But that reuse also means accumulated state
    from prior runs (devices that registered, canvas_layout rows, voice/note
    debris) survives — and tests that expect a clean slate fail.

    Uses a one-shot asyncpg connection in its own event loop. We avoid
    `infra.db.async_session` because importing it caches an engine whose
    connection pool is bound to whatever loop we run cleanup on — that
    pool then leaks into subsequent tests running in their own loops and
    triggers "Future attached to a different loop" errors deep inside
    SQLAlchemy.
    """
    import asyncio
    import os
    from urllib.parse import urlparse

    # Re-parse DATABASE_URL from .env (infra.config has already populated
    # os.environ for us via its module-load load_dotenv()).
    import infra.config  # noqa: F401

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(sync_url)
    if not parsed.hostname or not parsed.path:
        return

    try:
        import asyncpg
    except ImportError:
        return

    async def _run() -> None:
        conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            # Tables that grow per-run for the dedicated user. A missing
            # table is fine — fresh DBs skip it silently. Each DELETE
            # autocommits since asyncpg.execute outside a transaction is
            # autocommit.
            for tbl in ("canvas_layout", "note_chunks", "interactions", "devices"):
                try:
                    await conn.execute(
                        f"DELETE FROM {tbl} WHERE user_id = $1",
                        DEFAULT_USER_UUID_STR,
                    )
                except asyncpg.exceptions.UndefinedTableError:
                    pass
        finally:
            await conn.close()

    # New loop, closed promptly so SQLAlchemy's per-loop pool isn't
    # contaminated.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    except Exception as e:
        print(f"[e2e/wipe] DB cleanup skipped: {e}", flush=True)
    finally:
        loop.close()

    # Also wipe the engineer's per-user git workspace. Without this,
    # `_has_blocks_for(user_id)` stays truthy from previous runs and
    # `engineer_build()` suppresses the hello-stub fallback — the
    # request_new_block test then sees no mount event because the engineer
    # returns `[]` thinking the canvas is already populated.
    workspace = REPO_ROOT / "data" / "canvases" / DEFAULT_USER_UUID_STR
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)


def _wait_listening(port: int, timeout: float, name: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.2)
    raise RuntimeError(f"{name} on :{port} did not start within {timeout}s")


@pytest.fixture(scope="session")
def services() -> Iterator[dict]:
    """Boot all sidecars on a fresh BASE_PORT. Yields {base_port, shell_url, log_dir}."""
    if not VENV_PYTHON.exists():
        pytest.skip(f"venv python not found at {VENV_PYTHON}")

    base_port = _free_base_port()
    # Make in-process upstream_url() calls resolve to THIS e2e topology — e.g. a
    # test that calls a tool directly (tools.speak) which builds a
    # SiliconBrainClient internally. base_port() reads os.environ["BASE_PORT"]
    # dynamically, so setting it here points in-process calls at the e2e sidecars
    # instead of whatever happens to be on the default base port. Restored below.
    _prev_base_port = os.environ.get("BASE_PORT")
    os.environ["BASE_PORT"] = str(base_port)
    log_dir = REPO_ROOT / ".e2e-logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()

    env = os.environ.copy()
    env["BASE_PORT"] = str(base_port)
    # Force browser sidecar headless even if user has BROWSER_HEADED=1 in shell.
    env.pop("BROWSER_HEADED", None)
    # Make sure each subprocess can find the project root on sys.path.
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    # Use the deterministic fake LLM so e2e exercises the real DB path
    # without burning real API quota or hitting network.
    env["LLM_PROVIDER"] = "fake"
    # Disable note disk persistence so tests don't write to data/notes/.
    env["NOTES_PERSIST"] = "0"
    # Force the single-turn voice path even if the developer has
    # BWM_LEAD=1 in .env for normal dev. The teacher_tools e2e tests
    # exercise the LLM tool-loop directly; the lead pass strips the tool
    # palette on the fast line and would make them all fail. Overriding
    # in os.environ wins over .env because load_dotenv() respects existing
    # env by default.
    env["BWM_LEAD"] = "0"
    # Keep the sidecars on the skillforge DEFAULT-OFF baseline even though the
    # developer's .env points SKILLFORGE_EDGE_URL at the live local tuning
    # instance — a served snapshot could disable a tool or tune a description
    # and silently shift the manifest goldens. (In-process tests get the same
    # isolation from tests/conftest.py.)
    env["SKILLFORGE_EDGE_URL"] = ""

    procs: list[tuple[str, int, subprocess.Popen]] = []

    try:
        for name, offset in SIDECARS:
            port = base_port + offset
            log = open(log_dir / f"{name}.log", "wb")
            proc = subprocess.Popen(
                [str(VENV_PYTHON), "-m", f"services.{name}"],
                cwd=REPO_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if sys.platform != "win32" else None,
            )
            procs.append((name, port, proc))
            try:
                # Knowledge needs DB; browser needs Playwright; give them generous
                # ramp-up. Lighter sidecars come up in <2s.
                timeout = 25.0 if name in ("knowledge", "browser") else 12.0
                _wait_listening(port, timeout, name)
            except Exception:
                # Capture log tail before re-raising for debuggability.
                log.flush()
                log_path = log_dir / f"{name}.log"
                snippet = log_path.read_text(errors="replace")[-2000:]
                raise RuntimeError(
                    f"{name} on :{port} failed to start. Tail of log:\n{snippet}"
                )

        # Wipe accumulated rows for the dedicated test user before any test
        # runs. e2e reuses DEFAULT_USER_ID across runs (idempotent device
        # ids etc.), but tests like test_media_empty_inventory_for_fresh_user
        # need a known-empty starting state. UPSERTS in register() recreate
        # the rows tests actually need, so wiping is safe.
        _wipe_dedicated_user_state()

        yield {
            "base_port": base_port,
            "shell_url": f"http://127.0.0.1:{base_port}",
            "knowledge_url": f"http://127.0.0.1:{base_port + 2}",
            "browser_url": f"http://127.0.0.1:{base_port + 5}",
            "log_dir": log_dir,
        }
    finally:
        for name, _port, proc in procs:
            if proc.poll() is None:
                try:
                    if sys.platform != "win32":
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    else:
                        proc.terminate()
                except ProcessLookupError:
                    pass
        for _name, _port, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if sys.platform != "win32":
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except ProcessLookupError:
                    pass
        if _prev_base_port is None:
            os.environ.pop("BASE_PORT", None)
        else:
            os.environ["BASE_PORT"] = _prev_base_port


@pytest.fixture(scope="session")
def shell_url(services: dict) -> str:
    return services["shell_url"]


@pytest.fixture(scope="session")
def http(shell_url: str) -> Iterator[httpx.Client]:
    # trust_env=False: never route test traffic through a system HTTP proxy.
    with httpx.Client(base_url=shell_url, timeout=30.0, trust_env=False) as c:
        yield c


DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"

# Stable device ids used by _device_headers() helpers across e2e tests so
# the `devices` table doesn't grow one row per request. register() UPSERTs
# by device_id, so reusing these means each pytest run just bumps last_seen
# on the same rows instead of leaving dozens of new ghosts behind. Tests
# that need to verify multi-device behaviour pass explicit distinct ids.
E2E_DEVICE_ID = "11111111-1111-1111-1111-111111111111"
E2E_DEVICE_ID_ALT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="session")
def test_user_id(http: httpx.Client) -> str:
    """The default user seeded by `scripts/init_db.py`.

    Auth-required tests use this so the shell's verifier accepts them. Skips
    the whole module if the user can't be reached (DB / sidecars not up)."""
    resp = http.get("/api/users")
    if resp.status_code != 200:
        pytest.skip(f"DB unreachable: {resp.status_code} {resp.text[:200]}")
    if not any(u.get("id") == DEFAULT_USER_ID for u in resp.json()):
        pytest.skip("default user not seeded; run scripts/init_db.py")
    return DEFAULT_USER_ID


@pytest.fixture(scope="session")
def auth(test_user_id: str) -> dict[str, str]:
    """Headers for an authenticated request."""
    return {"X-User-Id": test_user_id}


@pytest.fixture
def fresh_engineer_workspace(test_user_id: str) -> None:
    """Wipe the dedicated user's per-user git workspace before this test.

    Some tests (notably `test_teacher_tool_request_new_block_mounts_block`)
    rely on the engineer's "hello-stub fallback" path, which only triggers
    when `agents.frontend_engineer.build._has_blocks_for(user_id)` is
    False. By the time those tests run, earlier tests in the same session
    have populated the workspace with blocks, so the fallback is
    suppressed and no mount event fires. This fixture restores the
    empty-workspace precondition.

    Function-scoped so it only fires for tests that explicitly opt in.
    """
    workspace = REPO_ROOT / "data" / "canvases" / test_user_id
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
