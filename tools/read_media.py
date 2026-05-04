"""read_media — the persona's perception call.

Returns one MediaPerception describing every canvas + voice the user has,
with each block's latest self-reported state and the persona's recent
voice utterances per device.

Layered on top of `list_media` (devices + canvas_layout + block sources)
plus the perception cache (block state + voice log). One read per call,
all dict lookups + a single DB select for the layout — cheap enough that
the persona can call it on every turn.

Filtering: pass `block_ids` and/or `device_ids` to narrow the response
when the persona only cares about a slice. Defaults to "everything",
which preserves the role `list_media` played before.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select

from infra.db import async_session
from infra.devices import registry as device_registry
from infra.perception import (
    BlockSummary,
    CanvasPerception,
    MediaPerception,
    VoicePerception,
    read_for_user,
)
from silicon_brain.models.canvas_layout import CanvasLayout

from agents.frontend_engineer import llm_engineer

from tools.list_media import _title_from_design_doc


async def read_media(
    user_id: UUID,
    block_ids: Optional[Iterable[str]] = None,
    device_ids: Optional[Iterable[UUID]] = None,
) -> MediaPerception:
    """Build a MediaPerception view for the user."""
    block_filter = set(block_ids) if block_ids else None
    device_filter = {str(d) for d in device_ids} if device_ids else None

    devices = await device_registry.list_for_user(user_id)

    sources = llm_engineer.list_blocks(user_id)
    title_by_id: dict[str, str | None] = {
        b.id: _title_from_design_doc(b.design_doc) for b in sources
    }

    async with async_session() as session:
        result = await session.execute(
            select(CanvasLayout).where(CanvasLayout.user_id == user_id)
        )
        layout_rows = list(result.scalars().all())

    blocks_by_device: dict[str, list[str]] = {}
    for row in layout_rows:
        blocks_by_device.setdefault(str(row.device_id), []).append(row.block_id)

    perc = read_for_user(user_id)
    state_by_device = perc["block_state"]    # {did: {bid: (state, ts)}}
    voice_log = perc["voice_log"]
    now = datetime.utcnow()

    canvases: list[CanvasPerception] = []
    voices: list[VoicePerception] = []

    for d in devices:
        did_s = str(d.device_id)
        if device_filter is not None and did_s not in device_filter:
            continue

        if d.capabilities.display:
            block_state_map = state_by_device.get(did_s, {})
            mounted_ids = blocks_by_device.get(did_s, [])

            # Union of mounted blocks + blocks that have reported state.
            # Reporting without being in canvas_layout shouldn't make the
            # block invisible — it just means the layout row hasn't been
            # written yet (race) or the block reports without being formally
            # mounted.
            seen_ids = list(dict.fromkeys(mounted_ids + list(block_state_map.keys())))

            block_summaries: list[BlockSummary] = []
            for bid in seen_ids:
                if block_filter is not None and bid not in block_filter:
                    continue
                state, ts = block_state_map.get(bid, (None, None))
                last_ago = (now - ts).total_seconds() if ts is not None else None
                block_summaries.append(BlockSummary(
                    id=bid,
                    title=title_by_id.get(bid),
                    state=state,
                    last_updated_s_ago=last_ago,
                ))

            canvases.append(CanvasPerception(
                device_id=d.device_id,
                device_class=d.device_class,
                online=d.online,
                blocks=block_summaries,
            ))

        if d.capabilities.speaker:
            recent = [u for u in voice_log if u.device_id == d.device_id]
            voices.append(VoicePerception(
                device_id=d.device_id,
                device_class=d.device_class,
                online=d.online,
                recent_utterances=recent,
            ))

    # Voice utterances with no device_id (broadcast or pre-multidevice
    # callers) attach to the first online voice; otherwise dropped from
    # per-device view but still in the raw log if anyone reads it.
    untagged = [u for u in voice_log if u.device_id is None]
    if untagged and voices:
        voices[0].recent_utterances = untagged + voices[0].recent_utterances

    return MediaPerception(user_id=user_id, canvases=canvases, voices=voices)


__all__ = ["read_media"]
