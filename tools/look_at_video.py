"""look_at_video — delegate video perception to the vision orchestrator.

Mirrors `look_at_image`: the persona is text-only and uses this tool when
the input is a video file or URL. The orchestrator extracts scene-cut
frames, transcribes the audio track, and merges them into a single
chronological timeline that the persona consumes as plain text.

Accepted source forms:
  * Local file path — anything ffmpeg can open (.mp4/.mov/.webm/.mkv/.mp3/…)
  * http(s) URL — fetched by ffmpeg directly. Frames are uploaded to the
    vision provider as base64 data URLs by the orchestrator.
"""
from __future__ import annotations

import json
from typing import Optional

from infra.media.video import describe_video_text
from infra.model.tools import ToolSpec
from uuid import UUID


async def look_at_video(
    video: str,
    question: Optional[str] = None,
    max_frames: int = 24,
) -> dict:
    """Return a timeline-formatted description of `video`."""
    description = await describe_video_text(
        video, prompt=question, max_frames=max_frames
    )
    return {"description": description}

def _make_look_at_video(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        video = (args.get("video") or "").strip()
        if not video:
            return json.dumps({"error": "video is required"})
        question_raw = args.get("question")
        question = (
            question_raw.strip()
            if isinstance(question_raw, str) and question_raw.strip()
            else None
        )
        max_frames_raw = args.get("max_frames")
        try:
            max_frames = int(max_frames_raw) if max_frames_raw is not None else 24
        except (TypeError, ValueError):
            return json.dumps({"error": "max_frames must be an integer"})
        max_frames = max(1, min(max_frames, 64))
        try:
            result = await look_at_video(video, question, max_frames=max_frames)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"video call failed: {e}"})
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="look_at_video",
        description=(
            "Delegate video perception to the vision pipeline. Use this "
            "for video files (mp4/mov/webm/mkv) or audio files (mp3/wav) "
            "— NOT for single still images, which go through "
            "`look_at_image`. Pass `video` as a local file path or "
            "http(s) URL. Optionally pass `question` to steer the "
            "per-frame prompt (e.g. 'what is the person writing?'). "
            "Returns `{description: str}` where the description is a "
            "chronological timeline interleaving visual descriptions "
            "and speech transcripts, e.g. "
            "`[00:00.0] vision: ...\\n[00:00.4–00:03.2] speech: \"...\"`. "
            "Slow: ffmpeg + many vision calls + Whisper transcription; "
            "expect 10–60s depending on clip length. Caps at "
            "`max_frames` vision calls (default 24, max 64)."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "video": {
                    "type": "string",
                    "description": (
                        "Local file path or http(s) URL to the video "
                        "or audio source."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": (
                        "Optional. What to look for in each frame. "
                        "Defaults to a general description."
                    ),
                },
                "max_frames": {
                    "type": "integer",
                    "description": (
                        "Optional. Cap on vision calls. Default 24, "
                        "max 64."
                    ),
                    "minimum": 1,
                    "maximum": 64,
                },
            },
            "required": ["video"],
            "additionalProperties": False,
        },
        executor=_make_look_at_video(user_id),
    )
