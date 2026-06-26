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

import json
import base64
import mimetypes
from pathlib import Path
from typing import Optional

from infra.model.vision import describe_image
from infra.model.tools import ToolSpec
from uuid import UUID


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

def _make_look_at_image(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        image = (args.get("image") or "").strip()
        if not image:
            return json.dumps({"error": "image is required"})
        question_raw = args.get("question")
        question = (
            question_raw.strip()
            if isinstance(question_raw, str) and question_raw.strip()
            else None
        )
        try:
            result = await look_at_image(image, question)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"vision call failed: {e}"})
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="look_at_image",
        description=(
            "Delegate visual perception to a vision model. You are "
            "text-only; this tool is your eyes. Pass `image` (a "
            "`data:image/png;base64,...` URL is preferred — http(s) "
            "URLs may fail due to provider region restrictions) plus "
            "an optional `question` to steer the description (e.g. "
            "'is the loading spinner still visible?', 'what does the "
            "error banner say?', 'are there non-blank pixels in the "
            "video region?'). Returns `{description: str}` — plain "
            "text you reason over as if the user had described the "
            "image themselves. Costs ~5–6s per call; use sparingly. "
            "Minimum image dimension is 14×14 pixels."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": (
                        "Image as a data URL (data:image/png;base64,...) "
                        "or http(s) URL."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": (
                        "Optional. What to look for. Defaults to a "
                        "general description."
                    ),
                },
            },
            "required": ["image"],
            "additionalProperties": False,
        },
        executor=_make_look_at_image(user_id),
    )
