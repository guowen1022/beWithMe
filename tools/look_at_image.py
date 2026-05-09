"""look_at_image — delegate visual perception to the vision model.

The main reasoning LLM (DeepSeek V4) is text-only. When the persona needs
to know what's actually shown in an image — a screenshot, a user-supplied
diagram, a thumbnail — it calls this tool. The vision facade
(`infra.model.vision`) sends the image to whichever provider is active
(Doubao Seed 2.0 Lite by default) and returns a textual description that
the persona then reasons over as plain text.

Accepted image forms:
  * `data:image/png;base64,...` — preferred for screenshots since the
    vision provider's network may not reach external URLs.
  * `https://...` — only works if the provider's region can reach the
    host. Volces' cn-beijing region cannot reach external image hosts;
    callers should base64-encode instead.
"""
from __future__ import annotations

from typing import Optional

from infra.model.vision import describe_image


async def look_at_image(image: str, question: Optional[str] = None) -> dict:
    """Return a textual description of `image`.

    `question` lets the caller steer the description ("what colors are in
    the top half?", "is the loader still visible?"). Default prompt asks
    for a general description.
    """
    description = await describe_image(image, question)
    return {"description": description}
