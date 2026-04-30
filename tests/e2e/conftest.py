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
import uuid
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
    ("shell", 0),  # shell last so upstreams are ready before it accepts traffic
]


def _free_base_port(start: int = 18000, span: int = 6) -> int:
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


@pytest.fixture(scope="session")
def shell_url(services: dict) -> str:
    return services["shell_url"]


@pytest.fixture(scope="session")
def http(shell_url: str) -> Iterator[httpx.Client]:
    # trust_env=False: never route test traffic through a system HTTP proxy.
    with httpx.Client(base_url=shell_url, timeout=30.0, trust_env=False) as c:
        yield c


@pytest.fixture(scope="session")
def test_user_id(http: httpx.Client) -> str:
    """A real user created via the public /api/users endpoint.

    Auth-required tests use this so the shell's verifier accepts them. Skips
    the whole module if Postgres is unreachable in this environment."""
    username = f"e2e-test-{uuid.uuid4().hex[:8]}"
    resp = http.post("/api/users", json={"username": username})
    if resp.status_code != 200:
        pytest.skip(f"cannot create test user (DB unreachable?): {resp.status_code} {resp.text[:200]}")
    return resp.json()["id"]


@pytest.fixture(scope="session")
def auth(test_user_id: str) -> dict[str, str]:
    """Headers for an authenticated request."""
    return {"X-User-Id": test_user_id}
