"""HTTP client for the local transcribe sidecar (services/transcribe).

Slices a 16 kHz mono WAV per voiced span, POSTs each slice to
`/api/transcribe`, and rebases the returned text into absolute video time.
Calls are issued sequentially because the sidecar holds an `_infer_lock`
internally — parallel uploads would just queue there anyway.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List

import httpx

from infra.media.vad import VoicedSpan
from infra.topology import upstream_url


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


def _slice_wav(wav_path: Path, start: float, end: float) -> bytes:
    """Return a self-contained WAV byte buffer for [start, end] of `wav_path`."""
    with wave.open(str(wav_path), "rb") as src:
        rate = src.getframerate()
        sampwidth = src.getsampwidth()
        nchannels = src.getnchannels()
        nframes_total = src.getnframes()

        start_frame = max(0, int(start * rate))
        end_frame = min(nframes_total, int(end * rate))
        if end_frame <= start_frame:
            return b""

        src.setpos(start_frame)
        pcm = src.readframes(end_frame - start_frame)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:
        dst.setnchannels(nchannels)
        dst.setsampwidth(sampwidth)
        dst.setframerate(rate)
        dst.writeframes(pcm)
    return buf.getvalue()


async def transcribe_spans(
    wav_path: Path,
    spans: List[VoicedSpan],
    language: str = "en",
    initial_prompt: str = "",
    timeout: float = 120.0,
) -> List[TranscriptSegment]:
    """Transcribe each voiced span; return absolute-time segments."""
    if not spans:
        return []
    url = f"{upstream_url('transcribe')}/api/transcribe"
    segments: List[TranscriptSegment] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for span in spans:
            blob = _slice_wav(wav_path, span.start, span.end)
            if not blob:
                continue
            files = {"file": ("span.wav", blob, "audio/wav")}
            data = {"language": language, "initial_prompt": initial_prompt}
            resp = await client.post(url, files=files, data=data)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"transcribe sidecar returned {resp.status_code}: "
                    f"{resp.text[:300]}"
                )
            text = (resp.json() or {}).get("text", "").strip()
            if text:
                segments.append(
                    TranscriptSegment(start=span.start, end=span.end, text=text)
                )
    return segments
