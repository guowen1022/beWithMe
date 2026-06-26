"""request_ui_block — teacher's tool for delegating UI work to the engineer.

Now backed by the per-user-git LLM engineer. Each call:
  1. Builds a BlockSpec carrying description + user_id.
  2. Runs the engineer LLM turn (reads workspace → writes blocks → commits).
  3. Fans out one UIUpdate(action="unmount") per deleted block id, then one
     UIUpdate(action="mount") per changed/added BlockSource. Mount with the
     same id replaces the existing block on the client.
  4. Reconciles `canvas_layout` rows so list_media() reflects which blocks
     live on which device's canvas.
  5. Returns the changed BlockSources so the caller can narrate.

When `target_device_id` is given, both the SSE event and the canvas_layout
update are scoped to that single device. Otherwise the event fans out to
every open SSE connection for the user, and canvas_layout rows are written
for every currently-online device of that user.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.frontend_engineer.build import build as engineer_build, engineer_turn
from infra.contracts.ui import BlockSource, BlockSpec, UIUpdate
from infra.db import async_session
from infra.devices import registry as device_registry
from infra.sandbox import validate_block_source
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from infra.devices.canvas_layout import CanvasLayout
from infra.model.tools import ToolSpec, ToolDomain


async def _ensure_valid(blocks: list[BlockSource]) -> None:
    """Sandbox-validate every block source before SSE-fanout. Raises
    ValueError on the first invalid block so the caller can surface the
    error to the LLM (engineer or teacher) and retry, instead of mounting
    a broken block on the user's canvas. Validator infra failures (Node
    missing, etc.) silently fall through — see infra.sandbox."""
    for block in blocks:
        err = await validate_block_source(block.source)
        if err:
            raise ValueError(
                f"engineer produced invalid block source for {block.id!r}: {err}"
            )


async def _online_device_ids(user_id: UUID) -> list[UUID]:
    devices = await device_registry.list_for_user(user_id)
    return [d.device_id for d in devices if d.online]


async def _record_mount(user_id: UUID, block_id: str, device_ids: list[UUID]) -> None:
    if not device_ids:
        return
    async with async_session() as session:
        for did in device_ids:
            stmt = (
                pg_insert(CanvasLayout)
                .values(user_id=user_id, device_id=did, block_id=block_id)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "device_id", "block_id"],
                )
            )
            await session.execute(stmt)
        await session.commit()


async def _record_unmount(
    user_id: UUID, block_id: str, device_ids: list[UUID] | None
) -> None:
    """Delete layout rows for this block. When device_ids is None, drop all
    rows for the block (matches a user-wide fan-out unmount)."""
    async with async_session() as session:
        stmt = CanvasLayout.__table__.delete().where(
            CanvasLayout.user_id == user_id,
            CanvasLayout.block_id == block_id,
        )
        if device_ids is not None:
            stmt = stmt.where(CanvasLayout.device_id.in_(device_ids))
        await session.execute(stmt)
        await session.commit()


async def request_ui_block(
    spec: BlockSpec,
    user_id: UUID,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    target_device_id: Optional[UUID] = None,
) -> list[BlockSource]:
    """Run an engineer turn and ship the resulting deltas over SSE.

    If `on_delta` is provided, the engineer's LLM output is streamed
    through it as it arrives — used by the canvas debug panel to surface
    "what the LLM is thinking" while it's still working.

    If `target_device_id` is provided, the mount/unmount events and the
    canvas_layout rows are scoped to that single device. Otherwise both
    fan out: the SSE event goes to every connection for the user, and
    layout rows are written for every currently-online device.
    """
    spec_with_user = spec.model_copy(update={"user_id": user_id})

    if target_device_id is not None:
        mount_targets = [target_device_id]
        unmount_targets: list[UUID] | None = [target_device_id]
    else:
        mount_targets = await _online_device_ids(user_id)
        unmount_targets = None  # broadcast unmount = drop layout for every device

    async def _send(event: UIUpdate) -> None:
        if target_device_id is not None:
            await enqueue_for_device(user_id, target_device_id, event)
        else:
            await enqueue_for_user(user_id, event)

    description = (spec.description or "").strip()
    if user_id and description:
        result = await engineer_turn(spec_with_user, on_delta=on_delta)
        if result is None or (not result.changed and not result.deleted):
            # Engineer produced nothing actionable. `engineer_build` knows
            # how to fall back to the hello stub for first-time users so
            # the smoke path (`/block hello` with the fake LLM) still
            # mounts something visible.
            blocks = await engineer_build(spec_with_user)
            await _ensure_valid(blocks)
            for block in blocks:
                await _send(UIUpdate(action="mount", block=block))
                await _record_mount(user_id, block.id, mount_targets)
            return blocks
        # Validate the engineer's output before fanning out. Any invalid
        # block aborts the whole batch — partial mounts of a multi-block
        # change leave the canvas in an inconsistent state.
        await _ensure_valid(result.changed)
        for bid in result.deleted:
            await _send(UIUpdate(action="unmount", block=BlockSource(id=bid, source="")))
            await _record_unmount(user_id, bid, unmount_targets)
        for block in result.changed:
            await _send(UIUpdate(action="mount", block=block))
            await _record_mount(user_id, block.id, mount_targets)
        return result.changed

    # Fallback path: the hello stub, no engineer turn.
    blocks = await engineer_build(spec_with_user)
    await _ensure_valid(blocks)
    for block in blocks:
        await _send(UIUpdate(action="mount", block=block))
        await _record_mount(user_id, block.id, mount_targets)
    return blocks

# Tripwire: descriptions matching any of these patterns are diagram-shaped
# requests and must go through `interactive_graph`, not the engineer LLM.
# We catch this server-side so the teacher gets immediate, deterministic
# feedback even if its prompt-side discipline slips.
_DIAGRAM_HINTS = re.compile(
    r"\b(flow ?chart|flow diagram|sequence diagram|class diagram|"
    r"er diagram|state diagram|"
    r"mind ?map|gantt|sankey|timeline|"
    r"step\s*\d|step[s]?\s*->|->\s*step|"
    r"hierarchy|tree of|relation(ship)?s? between|"
    r"diagram (of|showing|for)|chart (of|showing))\b",
    re.IGNORECASE,
)
# A separate check: arrow chains in the description are almost always a flow.
_ARROW_CHAIN = re.compile(r"(->|→|=>|-->).*?(->|→|=>|-->)")


def _make_request_new_block(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        description = (args.get("description") or "").strip()
        if not description:
            return json.dumps({"error": "description is required"})

        # Diagram-shaped requests must go through interactive_graph. The
        # engineer must never end up authoring per-step JS for a flow,
        # sequence, hierarchy, etc. — that's content, not code, and per-
        # step JS does not belong in the user's git workspace.
        if _DIAGRAM_HINTS.search(description) or _ARROW_CHAIN.search(description):
            return json.dumps({
                "error": (
                    "diagram-shaped request — use interactive_graph(name='...', "
                    "mermaid='flowchart LR ...') instead. request_new_block is "
                    "for novel interactive widgets only (sliders, simulations, "
                    "custom inputs); diagrams are content rendered by the "
                    "ephemeral interactive_graph surface, not code in the "
                    "user's workspace."
                )
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        spec = BlockSpec(description=description)
        try:
            blocks = await request_ui_block(spec, user_id, target_device_id=target_uuid)
        except ValueError as e:
            # Sandbox validation rejected the engineer's output. Surface
            # the message so the teacher's LLM can refine the description
            # (or the engineer agent rewrites on the next call).
            return json.dumps({"error": str(e)})
        return json.dumps({"mounted_block_ids": [b.id for b in blocks]})
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="request_new_block",
        description=(
            "Use **only** for novel *interactive widgets* — sliders, "
            "custom inputs, simulations, anything that needs fresh "
            "JavaScript. **DO NOT** use for diagrams (flows, "
            "sequences, charts, hierarchies, classes, mind maps, "
            "timelines): those go to `interactive_graph`. **DO NOT** "
            "use to display text or a passage: those go to "
            "`mount_template`. The engineer LLM authors code here — "
            "slow, and only justified for genuinely novel UI."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What the new block should do. 1-3 short sentences.",
                },
                "target_device_id": {
                    "type": "string",
                    "description": "Optional UUID; mount on this device only. Omit to fan out.",
                },
            },
            "required": ["description"],
            "additionalProperties": False,
        },
        executor=_make_request_new_block(user_id),
        domain=ToolDomain.CANVAS,
    )
