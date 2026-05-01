"""Engineer build path — entry point used by tools/request_ui_block.py.

Delegates to the LLM engineer (`llm_engineer.respond`), which reads the
user's per-user git workspace, writes/edits/deletes blocks, commits, and
returns the changed BlockSources for the SSE delta.

Backward-compat fallbacks:
  - If `spec.suggested_id == "hello"` and there's no description, return
    the hardcoded hello block (used by the `/block hello` smoke override
    so it works without an LLM call).
  - If the LLM engineer raises, fall back to the hardcoded hello so the
    delegation path always produces *something*.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from infra.contracts.ui import BlockSource, BlockSpec

from agents.frontend_engineer import llm_engineer


_HELLO_BLOCK_JS = """\
({
  id: 'hello',
  grid: { x: 60, y: 38, w: 40, h: 14 },
  content: 'Hello, World',
  style: {
    background: '#1f2937',
    color: '#f9fafb',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    fontSize: '24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '12px',
    border: '1px solid #374151',
    padding: '8px 16px',
    textAlign: 'center',
  },
  subscribes: ['greeting'],
  run(root, bus, cleanup) {
    const applyDevice = () => {
      const surface = root.closest('[data-device]');
      const device = surface ? surface.getAttribute('data-device') : 'desktop';
      root.style.fontSize = device === 'phone' ? '14px' : device === 'tablet' ? '20px' : '24px';
    };
    applyDevice();
    const obs = new MutationObserver(applyDevice);
    const surface = root.closest('[data-device]');
    if (surface) obs.observe(surface, { attributes: true, attributeFilter: ['data-device'] });
    cleanup(() => obs.disconnect());

    const unsub = bus.subscribe('greeting', (value) => {
      root.textContent = String(value ?? 'Hello, World');
    });
    cleanup(() => unsub());
  },
})
"""


def _hello_fallback() -> list[BlockSource]:
    return [BlockSource(
        id="hello",
        source=_HELLO_BLOCK_JS,
        design_doc="Hardcoded hello block (engineer fallback).",
    )]


async def build(spec: BlockSpec) -> list[BlockSource]:
    """Run one engineer turn for the user. Returns changed/added BlockSources.

    Deletions are *not* in the return value — the caller asks the engineer
    via `engineer_turn` directly when it needs delete events.
    """
    description = (spec.description or "").strip()

    # Explicit hello stub — used by the /block hello smoke override.
    if spec.suggested_id == "hello" and not description:
        return _hello_fallback()

    if spec.user_id is None:
        # No user context — can't run the per-user-git engineer. Hello stub.
        return _hello_fallback()

    try:
        result = await llm_engineer.respond(spec.user_id, description or "say hi")
    except Exception as e:
        print(f"[frontend_engineer.build] LLM engineer failed: {e}", flush=True)
        return _hello_fallback()

    if not result.changed and not result.deleted:
        # The LLM may have decided no UI change was needed. Surface the
        # hello stub so the user sees *something* and can iterate.
        return _hello_fallback() if not _has_blocks_for(spec.user_id) else []
    return result.changed


def _has_blocks_for(user_id) -> bool:
    """True if the user already has at least one block — avoids spamming the
    hello stub on no-op turns when the canvas already has content."""
    try:
        return bool(llm_engineer.list_blocks(user_id))
    except Exception:
        return False


async def engineer_turn(
    spec: BlockSpec,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
) -> "llm_engineer.EngineerResult | None":
    """Full turn shape — used by callers that also need the deleted ids,
    plan lines, and commit sha (e.g. for richer SSE narration).

    If `on_delta` is provided, the engineer streams its LLM output through
    it so the caller can forward chunks to the client as they arrive.
    """
    if spec.user_id is None or not (spec.description or "").strip():
        return None
    try:
        return await llm_engineer.respond(
            spec.user_id, spec.description.strip(), on_delta=on_delta
        )
    except Exception as e:
        print(f"[frontend_engineer.engineer_turn] failed: {e}", flush=True)
        return None
