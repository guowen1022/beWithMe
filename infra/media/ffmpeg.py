"""ffmpeg-driven extraction of scene-cut frames and 16 kHz mono audio.

Two passes (one for video, one for audio) keep the subprocess invocations
simple and the failure modes diagnosable — silent video skips the second
pass, audio-only files skip the first. The added overhead vs a single
combined invocation is negligible compared to a single vision call.

The frame-selection filter combines a scene-change term with a rate
floor and ceiling so the same code path serves both VOD and streaming:

    select='(gt(scene,SCENE) + gte(t-prev_selected_t, 1/MIN_HZ))
            * gte(t-prev_selected_t, 1/MAX_HZ)'

  - keep frame if SCENE-change OR enough time has passed since last keep
  - AND never keep faster than MAX_HZ
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# `showinfo` writes one line per selected frame to stderr at -loglevel info.
# The fields we care about are `n:` (index) and `pts_time:` (seconds).
_SHOWINFO_RE = re.compile(
    r"Parsed_showinfo.*?n:\s*(\d+).*?pts_time:\s*([\d.]+)"
)


@dataclass(frozen=True)
class ExtractedFrame:
    path: Path
    pts_time: float  # seconds from the start of the source


@dataclass(frozen=True)
class ExtractedMedia:
    frames: List[ExtractedFrame]  # may be empty (audio-only sources)
    audio_path: Optional[Path]    # None if source has no audio stream
    duration: float               # seconds; 0 if probe failed


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required for video understanding. "
            "Install with `brew install ffmpeg`."
        )
    if shutil.which("ffprobe") is None:
        raise RuntimeError(
            "ffprobe is required for video understanding. "
            "It ships with ffmpeg — install with `brew install ffmpeg`."
        )


async def _probe(src: str) -> Tuple[bool, bool, float]:
    """Return (has_video, has_audio, duration_seconds)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type:format=duration",
        "-of", "json",
        src,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {src!r}: "
            f"{stderr.decode('utf-8', 'replace')[:400]}"
        )
    info = json.loads(stdout.decode("utf-8", "replace") or "{}")
    streams = info.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration_raw = (info.get("format") or {}).get("duration") or "0"
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0
    return has_video, has_audio, duration


def _build_select_expr(
    scene_threshold: float, min_hz: float, max_hz: float
) -> str:
    floor_period = 1.0 / max(min_hz, 1e-6)
    ceiling_period = 1.0 / max(max_hz, 1e-6)
    # ffmpeg select expressions use commas as argument separators inside
    # filtergraph syntax, so any literal commas in the expression must be
    # escaped. Easier to use `+` (OR) and `*` (AND) which the documentation
    # endorses and which avoid the comma-escape problem.
    #
    # `prev_selected_t` is NaN before the first selected frame, so the
    # rate-floor and ceiling terms can't ever fire on frame 0 — we force
    # the first frame in unconditionally with `eq(n,0)`.
    return (
        f"select='eq(n\\,0)"
        f"+(gt(scene\\,{scene_threshold:.3f})"
        f"+gte(t-prev_selected_t\\,{floor_period:.3f}))"
        f"*gte(t-prev_selected_t\\,{ceiling_period:.3f})',"
        f"showinfo"
    )


async def _extract_frames(
    src: str,
    workdir: Path,
    scene_threshold: float,
    min_hz: float,
    max_hz: float,
) -> List[ExtractedFrame]:
    pattern = str(workdir / "frame_%04d.jpg")
    vf = _build_select_expr(scene_threshold, min_hz, max_hz)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-loglevel", "info",
        "-y",
        "-i", src,
        "-vf", vf,
        "-vsync", "vfr",
        "-q:v", "3",  # JPEG quality, 2–5 is the sweet spot
        pattern,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    stderr = stderr_bytes.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed: {stderr[-500:]}"
        )
    # Parse showinfo lines. Order in stderr matches frame_NNNN.jpg numbering
    # (1-indexed by ffmpeg's image2 muxer).
    pts_times = [float(m.group(2)) for m in _SHOWINFO_RE.finditer(stderr)]
    frames: List[ExtractedFrame] = []
    for idx, pts in enumerate(pts_times, start=1):
        candidate = workdir / f"frame_{idx:04d}.jpg"
        if candidate.exists():
            frames.append(ExtractedFrame(path=candidate, pts_time=pts))
    return frames


async def _extract_audio(src: str, workdir: Path) -> Path:
    out = workdir / "audio.wav"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", src,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(out),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg audio extraction failed: "
            f"{stderr_bytes.decode('utf-8', 'replace')[-500:]}"
        )
    return out


async def extract_media(
    src: str,
    workdir: Path,
    scene_threshold: float = 0.3,
    min_hz: float = 0.2,
    max_hz: float = 4.0,
) -> ExtractedMedia:
    """Pull frames + audio from a video/audio source.

    `src` is anything ffmpeg can open: a local path, an http(s) URL, or
    `pipe:0` for a live stream (future v2 streaming entry point).
    """
    _require_ffmpeg()
    workdir.mkdir(parents=True, exist_ok=True)
    has_video, has_audio, duration = await _probe(src)
    frames: List[ExtractedFrame] = []
    audio: Optional[Path] = None
    if has_video:
        frames = await _extract_frames(
            src, workdir, scene_threshold, min_hz, max_hz
        )
    if has_audio:
        audio = await _extract_audio(src, workdir)
    return ExtractedMedia(frames=frames, audio_path=audio, duration=duration)
