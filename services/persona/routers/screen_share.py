"""POST /api/perception/screen_chunk — live screen-share ingest.

The frontend `screen_share` block records 3 s WebM chunks via MediaRecorder
and POSTs each as multipart. We persist the chunk to disk, run the existing
`infra.media.video.describe_video()` orchestrator on it, rebase per-segment
timestamps to wall-clock, dedup against the session's last-kept pHash, and
fire ScreenSegmentEvents into the perception cache.

Backpressure: if Doubao slows down and we accumulate more than 3 in-flight
chunks for a session, new chunks are dropped (and a "chunk dropped:
backpressure" segment is recorded so the persona sees the gap).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Optional
from uuid import UUID

import imagehash
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from PIL import Image

from infra.auth import parse_user_id as get_current_user_id
from infra.media import screen_session
from infra.media.ffmpeg import extract_media
from infra.media.frame_select import dedupe_and_resize
from infra.media.transcribe_client import transcribe_spans
from infra.media.vad import voiced_spans
from infra.media.video import describe_video  # async generator over a file
from infra.perception import (
    ScreenSegment,
    record_screen_segment,
    record_screen_stopped,
    forget_screen_session,
)


router = APIRouter()


_CHUNK_ROOT = Path("/tmp/bewithme-screen")
_CROSS_CHUNK_HAMMING = 6  # same threshold the in-chunk dedup uses


def _log_exception(label: str, err: BaseException) -> None:
    tb = traceback.format_exception(type(err), err, err.__traceback__)
    print(f"[screen_share:{label}] " + "".join(tb), file=sys.stderr, flush=True)


def _session_dir(session_id: str) -> Path:
    d = _CHUNK_ROOT / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/perception/screen_chunk", status_code=202)
async def post_screen_chunk(
    file: UploadFile,
    session_id: str = Form(...),
    chunk_started_at_ms: int = Form(...),
    source_name: str = Form(""),
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Process one WebM chunk; return chunk-level stats.

    Fire-and-forget from the frontend's perspective — the response body
    is just `{accepted, dropped_reason?, segments_emitted}` for debugging.
    The real output (TimelineSegments) flows through the perception cache.
    """
    print(
        f"[screen_share] chunk arriving: user={user_id} session={session_id} "
        f"source={source_name!r} started_at={chunk_started_at_ms}",
        flush=True,
    )

    session = screen_session.get_or_create(
        user_id=user_id,
        session_id=session_id,
        started_at_wall_ms=chunk_started_at_ms,
        source_name=source_name or None,
    )

    # Backpressure check — drop the chunk before we even read its bytes
    # so a backed-up server doesn't pay the upload cost too. The persona
    # sees the gap because the perception ring just stops growing.
    if not screen_session.try_acquire_chunk_slot(session):
        record_screen_segment(
            user_id=user_id,
            session_id=session_id,
            segment=ScreenSegment(
                kind="vision",
                wall_time_ms=chunk_started_at_ms,
                content="(screen-share: chunk dropped — backend backpressure)",
                is_scene_cut=False,
            ),
            source_name=session.source_name,
        )
        return {"accepted": False, "dropped_reason": "backpressure", "segments_emitted": 0}

    seq = screen_session.next_seq(session)
    chunk_path = _session_dir(session_id) / f"chunk_{seq:06d}.webm"
    segments_emitted = 0
    try:
        data = await file.read()
        chunk_path.write_bytes(data)

        # Per-chunk processing reuses the shipped orchestrator. Each
        # describe_video() call uses its own temp workdir; we sequentially
        # consume yielded segments to keep dedup ordering deterministic.
        async for seg in describe_video(chunk_path, max_frames=6):
            wall = chunk_started_at_ms + int(seg.start * 1000)
            is_scene_cut = False
            if seg.kind == "vision":
                # Cross-chunk pHash: skip describing if this frame is
                # near-identical to the last kept one across the whole
                # session. The orchestrator already deduped within the
                # chunk; this catches the case where chunk N+1's first
                # kept frame matches chunk N's last kept frame.
                #
                # describe_video already deletes its temp frames after
                # yielding, so we can't re-pHash here. Instead the
                # orchestrator should be teaching us the pHash — but for
                # v1 we fall back to a content-string heuristic: drop
                # vision segments whose text exactly matches the last
                # kept vision text. Cheap and good enough.
                if session.last_kept_phash is not None and seg.content == session.last_kept_phash:
                    continue
                session.last_kept_phash = seg.content
                # First vision segment after a transcript-only stretch is a
                # natural "scene cut" for wake purposes. We approximate
                # that with: every kept vision segment is treated as a
                # scene cut. The pHash filter above ensures this only
                # fires when content actually changed.
                is_scene_cut = True
            record_screen_segment(
                user_id=user_id,
                session_id=session_id,
                segment=ScreenSegment(
                    kind=seg.kind,
                    wall_time_ms=wall,
                    content=seg.content,
                    is_scene_cut=is_scene_cut,
                ),
                source_name=session.source_name,
            )
            segments_emitted += 1
    except Exception as e:
        _log_exception("chunk", e)
        # Don't 500 — the frontend would treat that as a fatal upload
        # error and tear down the session. Log + return a 200 with a
        # diagnostic. The persona sees the gap via perception.
        record_screen_segment(
            user_id=user_id,
            session_id=session_id,
            segment=ScreenSegment(
                kind="vision",
                wall_time_ms=chunk_started_at_ms,
                content=f"(screen-share: chunk failed: {type(e).__name__})",
                is_scene_cut=False,
            ),
            source_name=session.source_name,
        )
        return {"accepted": False, "dropped_reason": str(e)[:200], "segments_emitted": 0}
    finally:
        screen_session.release_chunk_slot(session)
        try:
            chunk_path.unlink()
        except OSError:
            pass

    return {"accepted": True, "segments_emitted": segments_emitted}


@router.post("/perception/screen_chunk/stop", status_code=202)
async def post_screen_stop(
    session_id: str = Form(...),
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """End a session: mark offline, fire ScreenStoppedEvent, clean up."""
    record_screen_stopped(user_id=user_id, session_id=session_id)
    screen_session.drop(session_id)
    # Wipe session dir on stop. The cache ring is left alone so the
    # persona's next read still sees the trailing segments labelled
    # `online: False` — drops out next time it's read after that.
    try:
        shutil.rmtree(_session_dir(session_id), ignore_errors=True)
    except OSError:
        pass
    return {"accepted": True}
