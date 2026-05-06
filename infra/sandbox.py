"""Sandbox validation for content the persona/engineer LLMs hand to the
browser — block sources and (eventually) other source-shaped artifacts.

Two validators today, both implemented as Node subprocess scripts under
`scripts/`:

  * `validate_block_source(source)` — does a block source parse + match
    the EvaluatedBlock shape (id/grid/run)? Layer 1 = JS syntax,
    layer 2 = structural shape. Catches the bulk of "broken block
    reaches the user's canvas" cases.

  * (mermaid validation lives inline in workshop/canvas/tools/
    interactive_graph.py for historical reasons; can move here later.)

Failure modes are intentional:
  - returns None on success
  - returns a short error string on real validation failure
  - returns None on infra failure (Node missing, validator script
    missing, timeout) — better to let the user see a render error
    than to falsely block a valid source on a broken validator
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]
_BLOCK_VALIDATOR = _REPO_ROOT / "scripts" / "block-validate.mjs"
_VALIDATOR_TIMEOUT_S = 5.0


async def _run_node_validator(script_path: Path, source: str) -> Optional[str]:
    """Spawn a Node validator script, pipe `source` to stdin. Returns None
    on success (rc=0) or on infra failure; returns a short error string
    only on real validation failure (rc=1 with stderr text)."""
    if not script_path.exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError):
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(source.encode("utf-8")),
            timeout=_VALIDATOR_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    if proc.returncode == 0:
        return None
    msg = stderr.decode("utf-8", errors="replace").strip()
    if not msg:
        msg = stdout.decode("utf-8", errors="replace").strip() or (
            f"validator exited with code {proc.returncode}"
        )
    return msg


async def validate_block_source(source: str) -> Optional[str]:
    """Validate a parens-wrapped block-source JS expression. Returns None
    if the source parses and shapes up correctly; an error string if it
    has a syntax error or fails the structural shape check.

    Catches: bad JS (typos, unclosed braces), missing/wrong-typed `id`,
    `grid`, `run`, out-of-range grid coords. Does NOT call `run()` —
    runtime errors inside the block body still get caught by the
    frontend's reportBlockError path.
    """
    return await _run_node_validator(_BLOCK_VALIDATOR, source)


__all__ = ["validate_block_source"]
