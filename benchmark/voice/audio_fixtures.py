"""Audio fixture cache.

Generates WAVs for benchmark questions via the live `/api/speak` endpoint
(Kokoro TTS) on first run, then caches them on disk keyed by SHA-1 of the
text. Subsequent runs reuse the cached WAV so STT-side latency is
reproducible across runs (same audio in, same wall-clock variance only).

Fixtures live at `benchmark/voice/fixtures/audio/<sha>.wav`. The folder
is gitignored — the audio is regenerated on demand.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "audio"


def _audio_path(text: str) -> Path:
    sha = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return _FIXTURES_DIR / f"{sha}.wav"


async def ensure_audio(
    client: httpx.AsyncClient,
    text: str,
    headers: dict,
    voice: str = "af_heart",
) -> Path:
    """Return the path to a WAV of `text`, generating it via /api/speak
    on the first call. Raises if the speak service is unreachable.

    `headers` must include X-User-Id; the shell sidecar auth-gates /api/speak.
    """
    path = _audio_path(text)
    if path.exists() and path.stat().st_size > 100:
        return path
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    resp = await client.post(
        "/api/speak",
        headers=headers,
        json={"text": text, "voice": voice},
        timeout=60.0,
    )
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path
