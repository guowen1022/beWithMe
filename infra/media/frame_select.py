"""Perceptual-hash dedup + thumbnail downscale on top of ffmpeg-selected frames.

ffmpeg's scene-cut filter is content-blind: it can over-fire on micro-cuts
(camera shake, lighting flicker) and produce near-duplicate frames that the
vision model would describe identically. pHash with a Hamming-distance
threshold catches these without re-decoding.

Downscaling to ~768 px longest edge is a payload cut, not a quality cut:
Doubao (like every vision model) tokenizes images by tile, and sending 4 K
frames pays for tokens you don't need.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List

import imagehash
from PIL import Image

from infra.media.ffmpeg import ExtractedFrame


def _phash(path) -> imagehash.ImageHash:
    with Image.open(path) as img:
        return imagehash.phash(img)


def _downscale_inplace(path, max_edge: int) -> None:
    with Image.open(path) as img:
        w, h = img.size
        if max(w, h) <= max_edge:
            return
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        img.save(path, format="JPEG", quality=85, optimize=True)


def dedupe_and_resize(
    frames: List[ExtractedFrame],
    hamming_threshold: int = 6,
    max_edge: int = 768,
    max_frames: int = 24,
) -> List[ExtractedFrame]:
    """Drop near-duplicate frames, downscale survivors, cap count.

    `hamming_threshold` ~6 on a 64-bit pHash is the conventional "looks
    basically the same" line; lower = stricter (more frames kept).

    `max_frames` is enforced AFTER dedup by sampling evenly across the
    timeline — preserves coverage of the whole video at the cost of
    skipping some mid-frames on chaotic content.
    """
    if not frames:
        return []
    survivors: List[ExtractedFrame] = []
    last_hash: imagehash.ImageHash | None = None
    for f in frames:
        h = _phash(f.path)
        if last_hash is not None and (h - last_hash) < hamming_threshold:
            try:
                f.path.unlink()
            except OSError:
                pass
            continue
        survivors.append(f)
        last_hash = h

    if len(survivors) > max_frames:
        step = (len(survivors) - 1) / (max_frames - 1)
        picked_idx = {round(i * step) for i in range(max_frames)}
        dropped = [s for i, s in enumerate(survivors) if i not in picked_idx]
        survivors = [s for i, s in enumerate(survivors) if i in picked_idx]
        for f in dropped:
            try:
                f.path.unlink()
            except OSError:
                pass

    for f in survivors:
        _downscale_inplace(f.path, max_edge)

    return [replace(f) for f in survivors]
