"""set_talk_channel — turn the teacher's voice (spoken audio) and/or the
on-screen caption on or off for the user.

This is the conversational lever over the per-device talk-channel
preference that the `speak` tool reads. The persona calls it when the
user asks to change *how* they're addressed ("turn on voice", "mute the
audio", "stop showing captions", "just talk, no text on screen").

The stored preference is per device class (desktop / tablet / phone),
each one of `voice` | `text` | `both`. This tool maps the
(voice, caption) pair the user asked for onto that enum:

    voice=True,  caption=True  -> "both"
    voice=True,  caption=False -> "voice"
    voice=False, caption=True  -> "text"
    voice=False, caption=False -> rejected (there is no fully-silent
                                  channel; the teacher must reach the
                                  user somehow).

Pass `device_class` to scope the change to one device; omit it to apply
to all of the user's devices ("I want to hear you" usually means
everywhere). Reads the current preference first so untouched device
classes are preserved.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolSpec
from infra.silicon_brain_client import SiliconBrainClient

_DEVICE_CLASSES = ("desktop", "tablet", "phone")


def _channel_for(voice: bool, caption: bool) -> str:
    if voice and caption:
        return "both"
    if voice:
        return "voice"
    return "text"


def _make_set_talk_channel(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        voice = args.get("voice")
        caption = args.get("caption")
        if not isinstance(voice, bool) or not isinstance(caption, bool):
            return json.dumps({"error": "voice and caption must both be booleans"})
        if not voice and not caption:
            return json.dumps({
                "error": (
                    "at least one of voice/caption must be on — there is no "
                    "fully-silent channel. To stop talking, just don't call speak."
                )
            })
        channel = _channel_for(voice, caption)

        device_class = args.get("device_class")
        if device_class is not None and device_class not in _DEVICE_CLASSES:
            return json.dumps({
                "error": f"device_class must be one of {list(_DEVICE_CLASSES)} or omitted"
            })
        targets = [device_class] if device_class else list(_DEVICE_CLASSES)

        client = SiliconBrainClient()
        try:
            current = await client.get_talk_preference(user_id)
            new_pref = {dc: current.get(dc, "both") for dc in _DEVICE_CLASSES}
            for dc in targets:
                new_pref[dc] = channel
            updated = await client.update_talk_preference(
                user_id,
                desktop=new_pref["desktop"],
                tablet=new_pref["tablet"],
                phone=new_pref["phone"],
            )
        except Exception as e:  # noqa: BLE001 — surface as tool error, don't crash the turn
            return json.dumps({"error": f"failed to update talk preference: {e}"})
        finally:
            await client.aclose()

        return json.dumps({
            "ok": True,
            "channel": channel,
            "applied_to": targets,
            "preference": updated,
        })

    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="set_talk_channel",
        description=(
            "Turn your voice (spoken Kokoro audio) and/or the on-screen "
            "caption on or off for the user. Call this when the user asks to "
            "change HOW you talk to them: 'turn on voice', 'I can't hear you', "
            "'mute the audio', 'stop the captions', 'just talk, no text', "
            "'show captions too'. `voice` and `caption` are the desired ON/OFF "
            "state of each channel (at least one must be true). By default the "
            "change applies to all of the user's devices; pass `device_class` "
            "(read the active one from CURRENTLY ON CANVAS) to scope it to just "
            "that device. After changing it, your subsequent `speak` calls "
            "should use the matching channel."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "voice": {
                    "type": "boolean",
                    "description": "Whether spoken audio should play.",
                },
                "caption": {
                    "type": "boolean",
                    "description": "Whether the on-screen caption strip should show.",
                },
                "device_class": {
                    "type": "string",
                    "enum": list(_DEVICE_CLASSES),
                    "description": (
                        "Optional. Scope the change to one device class. "
                        "Omit to apply to all of the user's devices."
                    ),
                },
            },
            "required": ["voice", "caption"],
            "additionalProperties": False,
        },
        executor=_make_set_talk_channel(user_id),
    )


__all__ = ["build_spec"]
