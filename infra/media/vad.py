"""WebRTC VAD wrapper — pick out voiced ranges in a 16 kHz mono WAV.

webrtcvad operates on raw 16-bit PCM in fixed-size frames (10/20/30 ms).
We use 30 ms (the longest, cheapest option; voice detection doesn't need
finer granularity for our use case) and merge near-adjacent voiced frames
into spans so Whisper isn't asked to transcribe silence.

If webrtcvad isn't installed (optional dep), fall back to "one span covers
the whole file" — Whisper handles silence fine, we just skip the savings.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


_FRAME_MS = 30
_SAMPLE_RATE = 16000  # webrtcvad supports 8/16/32/48 kHz
_BYTES_PER_SAMPLE = 2  # s16


@dataclass(frozen=True)
class VoicedSpan:
    start: float  # seconds from start of WAV
    end: float


def _try_import_vad():
    try:
        import webrtcvad  # type: ignore
        return webrtcvad
    except Exception:
        return None


def voiced_spans(
    wav_path: Path,
    aggressiveness: int = 2,
    merge_gap_seconds: float = 0.3,
    min_span_seconds: float = 0.2,
) -> List[VoicedSpan]:
    """Return the voiced time-ranges in a 16 kHz mono s16 WAV.

    `aggressiveness` 0–3; 2 is the documented "moderate" default. Higher
    values reject more borderline frames (good for noisy environments).
    `merge_gap_seconds` collapses voiced frames separated by short silences
    so one sentence doesn't shatter into ten Whisper calls.
    """
    webrtcvad = _try_import_vad()
    if webrtcvad is None:
        with wave.open(str(wav_path), "rb") as wf:
            duration = wf.getnframes() / float(wf.getframerate() or 1)
        return [VoicedSpan(0.0, duration)] if duration > 0 else []

    vad = webrtcvad.Vad(aggressiveness)
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != _BYTES_PER_SAMPLE:
            raise ValueError(
                f"voiced_spans requires 16-bit mono PCM; got "
                f"channels={wf.getnchannels()} sampwidth={wf.getsampwidth()}"
            )
        rate = wf.getframerate()
        if rate != _SAMPLE_RATE:
            raise ValueError(f"voiced_spans requires {_SAMPLE_RATE} Hz; got {rate}")
        pcm = wf.readframes(wf.getnframes())

    bytes_per_frame = int(rate * (_FRAME_MS / 1000.0)) * _BYTES_PER_SAMPLE
    frame_seconds = _FRAME_MS / 1000.0
    spans: List[VoicedSpan] = []
    cur_start: Optional[float] = None
    last_voiced_end: float = 0.0

    for i in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame):
        frame = pcm[i:i + bytes_per_frame]
        t = (i / bytes_per_frame) * frame_seconds
        try:
            is_voice = vad.is_speech(frame, rate)
        except Exception:
            break
        if is_voice:
            if cur_start is None:
                cur_start = t
            last_voiced_end = t + frame_seconds
        else:
            if cur_start is not None and (t - last_voiced_end) > merge_gap_seconds:
                if last_voiced_end - cur_start >= min_span_seconds:
                    spans.append(VoicedSpan(cur_start, last_voiced_end))
                cur_start = None
    if cur_start is not None and last_voiced_end - cur_start >= min_span_seconds:
        spans.append(VoicedSpan(cur_start, last_voiced_end))
    return spans
