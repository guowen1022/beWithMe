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

from typing import Optional

from infra.media.video import describe_video_text


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
