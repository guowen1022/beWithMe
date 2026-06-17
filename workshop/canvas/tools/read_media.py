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

from infra.devices import registry as device_registry
from infra.perception import (
    BlockSummary,
    CanvasPerception,
    MediaPerception,
    ScreenPerception,
    VoicePerception,
    read_for_user,
)
from infra.devices.delivery import mounted_block_ids

from agents.frontend_engineer import llm_engineer

from workshop.canvas.tools.list_media import _title_from_design_doc
from infra.model.tools import ToolSpec


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

    # Authoritative lifecycle source: in-memory mount tracker, flipped
    # synchronously on every UIUpdate fan-out. No DB query, no race
    # against the perception cache.
    blocks_by_device: dict[str, list[str]] = mounted_block_ids(user_id)

    perc = read_for_user(user_id)
    state_by_device = perc["block_state"]    # {did: {bid: (state, ts)}}
    voice_log = perc["voice_log"]
    screen_sessions = perc.get("screen_sessions", {})  # {sid: {online, source_name, segments}}
    now = datetime.utcnow()

    canvases: list[CanvasPerception] = []
    voices: list[VoicePerception] = []
    screens: list[ScreenPerception] = [
        ScreenPerception(
            session_id=sid,
            online=info.get("online", False),
            source_name=info.get("source_name"),
            recent_segments=info.get("segments", []),
        )
        for sid, info in screen_sessions.items()
    ]

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

    return MediaPerception(user_id=user_id, canvases=canvases, voices=voices, screens=screens)


__all__ = ["read_media", "build_spec"]

def _make_read_media(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_ids = args.get("block_ids") or None
        device_ids_raw = args.get("device_ids") or None
        device_ids = None
        if device_ids_raw:
            try:
                device_ids = [UUID(d) for d in device_ids_raw]
            except (ValueError, TypeError):
                return json.dumps({"error": "device_ids must be valid UUIDs"})
        perc = await read_media(user_id, block_ids=block_ids, device_ids=device_ids)

        # Compact serialisation — keep only what the persona reasons over.
        canvases = []
        for c in perc.canvases:
            canvases.append({
                "device_id": str(c.device_id),
                "device_class": c.device_class,
                "online": c.online,
                "blocks": [
                    {
                        "id": b.id,
                        "title": b.title,
                        "state": (b.state.model_dump() if b.state else None),
                        "last_updated_s_ago": (
                            round(b.last_updated_s_ago, 1)
                            if b.last_updated_s_ago is not None else None
                        ),
                    }
                    for b in c.blocks
                ],
            })
        voices = []
        for v in perc.voices:
            voices.append({
                "device_id": str(v.device_id),
                "device_class": v.device_class,
                "online": v.online,
                "recent_utterances": [
                    {
                        "text": u.text,
                        "voice": u.voice,
                        "played_at": u.played_at.isoformat(),
                    }
                    for u in v.recent_utterances[-5:]   # last 5 — context-friendly
                ],
            })
        return json.dumps({"canvases": canvases, "voices": voices})
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="read_media",
        description=(
            "Read what the user is currently receiving — every canvas's "
            "mounted blocks (with each block's current self-reported "
            "state: what it shows, whether the user has it focused) and "
            "every voice device (with what you've recently said on it). "
            "Use this whenever your next action depends on what the user "
            "is actually looking at, hearing, or has highlighted. Pass "
            "no arguments to read everything; pass block_ids/device_ids "
            "to narrow the response. Each block's state has fields: "
            "kind (e.g. 'pdf', 'snapshot', 'browser'), content (one-line "
            "summary), focus ('active' = user attention here, 'visible', "
            "'background'), extra (block-specific structured data)."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "block_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. Only return state for these block ids.",
                },
                "device_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. Only return canvases/voices for these device UUIDs.",
                },
            },
            "additionalProperties": False,
        },
        executor=_make_read_media(user_id),
    )
