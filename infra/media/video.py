"""Video understanding orchestrator — frames + transcript → interleaved timeline.

Design notes:
  * Frame-extraction path, not native Doubao `input_video`. Reuses the
    provider-dispatched `describe_image()` per kept frame, so this module
    works against any vision provider transparently.
  * `describe_video` is an async generator that yields TimelineSegments in
    chronological order. v1 callers use `describe_video_text` which
    collects and joins; a future streaming sidecar can forward segments
    as they arrive without touching this module.
"""
from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, List, Literal, Optional, Union

from infra.media.ffmpeg import ExtractedFrame, extract_media
from infra.media.frame_select import dedupe_and_resize
from infra.media.transcribe_client import TranscriptSegment, transcribe_spans
from infra.media.vad import VoicedSpan, voiced_spans
from infra.model.vision import describe_image


_FRAME_CONCURRENCY = 4  # parallel vision calls; Doubao tolerates this fine
_DEFAULT_PROMPT = (
    "Describe what is shown in this video frame in one to two sentences. "
    "Focus on what is happening, not metadata."
)


@dataclass(frozen=True)
class TimelineSegment:
    kind: Literal["vision", "speech"]
    start: float
    end: float  # == start for vision (point-in-time)
    content: str


def _frame_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


async def _describe_frame(
    frame: ExtractedFrame, prompt: str, sem: asyncio.Semaphore
) -> TimelineSegment:
    async with sem:
        data_url = _frame_to_data_url(frame.path)
        text = await describe_image(data_url, prompt)
    return TimelineSegment(
        kind="vision",
        start=frame.pts_time,
        end=frame.pts_time,
        content=text.strip(),
    )


async def describe_video(
    src: Union[str, Path],
    prompt: Optional[str] = None,
    max_frames: int = 24,
    scene_threshold: float = 0.3,
    min_hz: float = 0.2,
    max_hz: float = 4.0,
    hamming_threshold: int = 6,
    max_edge: int = 768,
    keep_workdir: bool = False,
) -> AsyncIterator[TimelineSegment]:
    """Yield TimelineSegments in chronological order.

    Structure (async generator) is deliberately compatible with a future
    streaming caller: a v2 entry point can `async for seg in describe_video(...)`
    and forward each segment to a client as it arrives. v1 callers wrap
    via `describe_video_text`.
    """
    src_str = str(src)
    workdir = Path(tempfile.mkdtemp(prefix="bewithme-video-"))
    try:
        media = await extract_media(
            src_str,
            workdir,
            scene_threshold=scene_threshold,
            min_hz=min_hz,
            max_hz=max_hz,
        )

        kept_frames = dedupe_and_resize(
            media.frames,
            hamming_threshold=hamming_threshold,
            max_edge=max_edge,
            max_frames=max_frames,
        )

        async def _all_vision() -> List[TimelineSegment]:
            sem = asyncio.Semaphore(_FRAME_CONCURRENCY)
            effective_prompt = prompt or _DEFAULT_PROMPT
            tasks = [_describe_frame(f, effective_prompt, sem) for f in kept_frames]
            return await asyncio.gather(*tasks) if tasks else []

        async def _all_transcript() -> List[TranscriptSegment]:
            if media.audio_path is None:
                return []
            spans: List[VoicedSpan] = await asyncio.to_thread(
                voiced_spans, media.audio_path
            )
            if not spans:
                return []
            return await transcribe_spans(media.audio_path, spans)

        vision_task = asyncio.create_task(_all_vision())
        transcript_task = asyncio.create_task(_all_transcript())
        vision_segments, transcript_segments = await asyncio.gather(
            vision_task, transcript_task
        )

        speech_segments = [
            TimelineSegment(
                kind="speech",
                start=s.start,
                end=s.end,
                content=s.text,
            )
            for s in transcript_segments
        ]
        merged: List[TimelineSegment] = sorted(
            [*vision_segments, *speech_segments],
            key=lambda s: (s.start, 0 if s.kind == "vision" else 1),
        )
        for seg in merged:
            yield seg
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _format_time(t: float) -> str:
    minutes = int(t // 60)
    seconds = t - 60 * minutes
    return f"{minutes:02d}:{seconds:04.1f}"


def _segment_to_line(seg: TimelineSegment) -> str:
    if seg.kind == "vision":
        return f"[{_format_time(seg.start)}] vision: {seg.content}"
    return (
        f"[{_format_time(seg.start)}–{_format_time(seg.end)}] "
        f"speech: {seg.content}"
    )


async def describe_video_text(
    src: Union[str, Path],
    prompt: Optional[str] = None,
    max_frames: int = 24,
) -> str:
    """Collect every TimelineSegment and join into a readable timeline."""
    lines: List[str] = []
    async for seg in describe_video(src, prompt=prompt, max_frames=max_frames):
        lines.append(_segment_to_line(seg))
    if not lines:
        return "(no extractable content — source has no decodable video or audio)"
    return "\n".join(lines)


__all__ = ["TimelineSegment", "describe_video", "describe_video_text"]
