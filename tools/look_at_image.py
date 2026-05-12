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
  * Absolute local file path (e.g. from /api/media/upload) — the tool
    reads the file off disk and base64-encodes it before calling the
    provider. This is the path used by the user-upload flow.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Optional

from infra.model.vision import describe_image


def _local_file_to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


async def look_at_image(image: str, question: Optional[str] = None) -> dict:
    """Return a textual description of `image`."""
    if not image.startswith("data:") and not image.startswith("http"):
        p = Path(image)
        if p.is_file():
            image = _local_file_to_data_url(p)
    description = await describe_image(image, question)
    return {"description": description}
