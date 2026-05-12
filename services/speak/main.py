"""Speak sidecar — :BASE_PORT+4.

Local TTS via kokoro-onnx. Loads the model in lifespan so the first request
streams immediately.

Run standalone:
    python -m services.speak
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import time
import wave
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from infra.topology import service_port


# Kokoro config is local to this sidecar — no other module needs these paths.
load_dotenv()


class SpeakSettings(BaseSettings):
    # Local TTS (kokoro-onnx). Models live under beWithMe's own app-support dir.
    kokoro_model_path: str = (
        "/Users/weng/Library/Application Support/beWithMe/models/kokoro/kokoro-v1.0.onnx"
    )
    kokoro_voices_path: str = (
        "/Users/weng/Library/Application Support/beWithMe/models/kokoro/voices-v1.0.bin"
    )
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    kokoro_lang: str = "en-us"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = SpeakSettings()


router = APIRouter()


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float | None = None
    lang: str | None = None


_kokoro = None
_kokoro_lock = asyncio.Lock()
# Same caution as whisper.cpp — serialize inference on a single Kokoro
# instance to avoid tripping internal non-thread-safe state.
_infer_lock = asyncio.Lock()


def _load_kokoro():
    """Blocking; call inside asyncio.to_thread."""
    from kokoro_onnx import Kokoro

    return Kokoro(settings.kokoro_model_path, settings.kokoro_voices_path)


async def _get_kokoro():
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    async with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro
        for path in (settings.kokoro_model_path, settings.kokoro_voices_path):
            if not os.path.isfile(path):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Kokoro model missing at {path!r}. "
                        "Download kokoro-v1.0.onnx and voices-v1.0.bin from "
                        "https://github.com/thewh1teagle/kokoro-onnx/releases "
                        "to the configured paths, or override "
                        "KOKORO_MODEL_PATH / KOKORO_VOICES_PATH."
                    ),
                )
        _kokoro = await asyncio.to_thread(_load_kokoro)
        return _kokoro


def _synthesize(kokoro, text: str, voice: str, speed: float, lang: str) -> bytes:
    import numpy as np

    audio, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _synthesize_pcm(kokoro, text: str, voice: str, speed: float, lang: str) -> tuple[bytes, int]:
    """Synthesize a chunk and return raw little-endian 16-bit mono PCM + sample rate."""
    import numpy as np

    audio, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype("<i2")
    return pcm16.tobytes(), int(sample_rate)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Split a too-long sentence on clause/word boundaries."""
    if len(piece) <= max_chars:
        return [piece]
    out: list[str] = []
    subs = [s.strip() for s in re.split(r"(?<=[,;:])\s+", piece) if s.strip()]
    buf = ""
    for s in subs:
        if len(s) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            words = s.split()
            wbuf = ""
            for w in words:
                if len(wbuf) + len(w) + 1 > max_chars and wbuf:
                    out.append(wbuf)
                    wbuf = w
                else:
                    wbuf = f"{wbuf} {w}".strip()
            if wbuf:
                out.append(wbuf)
            continue
        if len(buf) + len(s) + 1 > max_chars and buf:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out


def _split_sentences(text: str, max_chars: int = 300) -> list[str]:
    out: list[str] = []
    for piece in _SENTENCE_SPLIT.split(text.strip()):
        piece = piece.strip()
        if not piece:
            continue
        out.extend(_split_long(piece, max_chars))
    return out or [text.strip()]


@router.post("/speak")
async def speak(body: SpeakRequest):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > 4000:
        raise HTTPException(status_code=413, detail="text too long (max 4000 chars)")

    kokoro = await _get_kokoro()
    voice = body.voice or settings.kokoro_voice
    speed = body.speed if body.speed is not None else settings.kokoro_speed
    lang = body.lang or settings.kokoro_lang

    async with _infer_lock:
        wav_bytes = await asyncio.to_thread(_synthesize, kokoro, text, voice, speed, lang)
    return Response(content=wav_bytes, media_type="audio/wav")


@router.post("/speak/stream")
async def speak_stream(body: SpeakRequest):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > 4000:
        raise HTTPException(status_code=413, detail="text too long (max 4000 chars)")

    kokoro = await _get_kokoro()
    voice = body.voice or settings.kokoro_voice
    speed = body.speed if body.speed is not None else settings.kokoro_speed
    lang = body.lang or settings.kokoro_lang

    chunks = _split_sentences(text)

    # Synthesize the first chunk eagerly so we can set the sample-rate header
    # before the response body starts flowing.
    first_chunk_t0 = time.perf_counter()
    async with _infer_lock:
        first_pcm, sample_rate = await asyncio.to_thread(
            _synthesize_pcm, kokoro, chunks[0], voice, speed, lang
        )
    first_chunk_ms = round((time.perf_counter() - first_chunk_t0) * 1000, 2)

    async def gen():
        yield first_pcm
        for chunk in chunks[1:]:
            async with _infer_lock:
                pcm, _ = await asyncio.to_thread(
                    _synthesize_pcm, kokoro, chunk, voice, speed, lang
                )
            yield pcm

    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Audio-Format": "pcm_s16le_mono",
            "X-First-Chunk-Ms": str(first_chunk_ms),
            "Cache-Control": "no-store",
        },
    )


async def prewarm() -> None:
    """Best-effort: load Kokoro at startup so the first /speak is fast."""
    for path in (settings.kokoro_model_path, settings.kokoro_voices_path):
        if not os.path.isfile(path):
            print(
                f"[speak] kokoro model missing at {path!r}; "
                "voice output will return 503 until the file is present.",
                flush=True,
            )
            return
    try:
        await _get_kokoro()
        print(
            f"[speak] kokoro model warmed ({settings.kokoro_voice}, {settings.kokoro_lang})",
            flush=True,
        )
    except Exception as err:  # noqa: BLE001
        print(f"[speak] prewarm failed: {err}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await prewarm()
    yield


app = FastAPI(title="beWithMe speak", lifespan=lifespan)
app.include_router(router, prefix="/api")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.speak.main:app",
        host="0.0.0.0",
        port=service_port("speak"),
        reload=False,
    )


if __name__ == "__main__":
    main()
