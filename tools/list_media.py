"""list_media — the teacher's inventory call.

Returns one MediaInventory describing every canvas + voice the teacher owns
for the given user. Reads:

  * `infra.devices.registry` for live + last-seen device rows.
  * `silicon_brain.models.canvas_layout.CanvasLayout` for which blocks are
    currently mounted on which device.
  * `agents.frontend_engineer.llm_engineer.list_blocks` for the full set of
    block sources in the user's git workspace (so we can resolve titles).

In P1 there is no LLM tool-loop yet — call this directly from Python (or
hit `/api/persona/teacher/media` for browser/curl debugging).
"""
from __future__ import annotations

from uuid import UUID

from infra.contracts.devices import (
    Canvas,
    CanvasBlockSummary,
    MediaInventory,
    Voice,
)
from infra.devices import registry as device_registry
from services.persona.routers.dynamic import mounted_block_ids

from agents.frontend_engineer import llm_engineer


def _title_from_design_doc(design_doc: str | None) -> str | None:
    """Pull the first non-blank line as a title; strip leading markdown #s."""
    if not design_doc:
        return None
    for raw in design_doc.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return None


async def list_media(user_id: UUID) -> MediaInventory:
    devices = await device_registry.list_for_user(user_id)

    # Resolve block titles once from the user's git workspace; canvas_layout
    # rows only carry block_ids.
    sources = llm_engineer.list_blocks(user_id)
    title_by_id: dict[str, str | None] = {
        b.id: _title_from_design_doc(b.design_doc) for b in sources
    }

    # device_id (str) → list of block_ids — read from the in-memory
    # mount tracker maintained by services.persona.routers.dynamic on
    # every UIUpdate fan-out. No DB query.
    blocks_by_device: dict[str, list[str]] = mounted_block_ids(user_id)

    canvases: list[Canvas] = []
    voices: list[Voice] = []
    for d in devices:
        if d.capabilities.display:
            block_ids = blocks_by_device.get(str(d.device_id), [])
            blocks = [
                CanvasBlockSummary(id=bid, title=title_by_id.get(bid))
                for bid in block_ids
            ]
            canvases.append(
                Canvas(
                    device_id=d.device_id,
                    device_class=d.device_class,
                    online=d.online,
                    blocks=blocks,
                )
            )
        if d.capabilities.speaker:
            voices.append(
                Voice(
                    device_id=d.device_id,
                    device_class=d.device_class,
                    online=d.online,
                )
            )

    return MediaInventory(user_id=user_id, canvases=canvases, voices=voices)


__all__ = ["list_media"]
