"""request_ui_block — teacher's tool for delegating UI work to the engineer.

Now backed by the per-user-git LLM engineer. Each call:
  1. Builds a BlockSpec carrying description + user_id.
  2. Runs the engineer LLM turn (reads workspace → writes blocks → commits).
  3. Fans out one UIUpdate(action="unmount") per deleted block id, then one
     UIUpdate(action="mount") per changed/added BlockSource. Mount with the
     same id replaces the existing block on the client.
  4. Returns the changed BlockSources so the caller can narrate.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional
from uuid import UUID

from agents.frontend_engineer.build import build as engineer_build, engineer_turn
from infra.contracts.ui import BlockSource, BlockSpec, UIUpdate
from services.persona.routers.dynamic import enqueue_for_user


async def request_ui_block(
    spec: BlockSpec,
    user_id: UUID,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
) -> list[BlockSource]:
    """Run an engineer turn and ship the resulting deltas over SSE.

    If `on_delta` is provided, the engineer's LLM output is streamed
    through it as it arrives — used by the canvas debug panel to surface
    "what the LLM is thinking" while it's still working.
    """
    spec_with_user = spec.model_copy(update={"user_id": user_id})

    # Use engineer_turn when we have description + user — it gives us the
    # deletion list. Fall back to the simpler build() for the hello stub
    # path (no description / no user).
    description = (spec.description or "").strip()
    if user_id and description:
        result = await engineer_turn(spec_with_user, on_delta=on_delta)
        if result is None:
            blocks = await engineer_build(spec_with_user)
            for block in blocks:
                await enqueue_for_user(user_id, UIUpdate(action="mount", block=block))
            return blocks
        # Unmount deletions first so a replace-via-id doesn't race.
        for bid in result.deleted:
            await enqueue_for_user(
                user_id,
                UIUpdate(action="unmount", block=BlockSource(id=bid, source="")),
            )
        for block in result.changed:
            await enqueue_for_user(user_id, UIUpdate(action="mount", block=block))
        return result.changed

    # Fallback path: the hello stub, no engineer turn.
    blocks = await engineer_build(spec_with_user)
    for block in blocks:
        await enqueue_for_user(user_id, UIUpdate(action="mount", block=block))
    return blocks
