"""Transcribe sidecar — :BASE_PORT+3.

Local Whisper transcription via pywhispercpp. Loads a GGML model in lifespan
(best-effort) so the first /transcribe request is fast.

Run standalone:
    python -m services.transcribe
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from pydantic_settings import BaseSettings

from infra.topology import service_port


# Whisper config is local to this sidecar — no other module needs these paths.
load_dotenv()


class TranscribeSettings(BaseSettings):
    # Local Whisper (pywhispercpp). Reuses Superwhisper's model by default.
    whisper_model_path: str = (
        "/Users/weng/Library/Application Support/Superwhisper/ggml-small.bin"
    )
    whisper_threads: int = 4

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = TranscribeSettings()


router = APIRouter()

_model = None
_model_lock = asyncio.Lock()
# whisper.cpp is NOT thread-safe across concurrent transcribe() calls on a
# single Model. Without this serialization, concurrent calls trip
# GGML_ASSERT and kill the process.
_infer_lock = asyncio.Lock()


def _load_model():
    """Load the pywhispercpp model. Blocking; call inside asyncio.to_thread."""
    from pywhispercpp.model import Model

    return Model(
        settings.whisper_model_path,
        n_threads=settings.whisper_threads,
        print_progress=False,
        print_realtime=False,
        print_timestamps=False,
    )


async def _get_model():
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        if not os.path.isfile(settings.whisper_model_path):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Whisper model not found at "
                    f"{settings.whisper_model_path!r}. Set WHISPER_MODEL_PATH "
                    "in .env to a ggml-*.bin file."
                ),
            )
        _model = await asyncio.to_thread(_load_model)
        return _model


async def prewarm() -> None:
    """Eagerly load the Whisper model at startup. Best-effort — logs and
    returns on failure so the sidecar can still boot without voice."""
    if not os.path.isfile(settings.whisper_model_path):
        print(
            f"[transcribe] whisper model missing at {settings.whisper_model_path!r}; "
            "voice input will return 503 until WHISPER_MODEL_PATH is set.",
            flush=True,
        )
        return
    try:
        await _get_model()
        print(f"[transcribe] whisper model warmed: {settings.whisper_model_path}", flush=True)
    except Exception as err:  # noqa: BLE001
        print(f"[transcribe] prewarm failed: {err}", flush=True)


def _decode_to_wav(src_path: str, dst_path: str) -> None:
    """ffmpeg: any container -> 16 kHz mono s16 WAV for whisper.cpp."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            src_path,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            dst_path,
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"ffmpeg decode failed: {proc.stderr.decode('utf-8', 'replace')[:500]}",
        )


def _transcribe_sync(model, wav_path: str, language: str, initial_prompt: str) -> str:
    kwargs = {"language": language}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments = model.transcribe(wav_path, **kwargs)
    return "".join(seg.text for seg in segments).strip()


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("en"),
    initial_prompt: str = Form(""),
):
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="ffmpeg not installed")

    model = await _get_model()

    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    src_fd, src_path = tempfile.mkstemp(suffix=suffix)
    os.close(src_fd)
    wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav_fd)

    try:
        data = await file.read()
        with open(src_path, "wb") as f:
            f.write(data)

        await asyncio.to_thread(_decode_to_wav, src_path, wav_path)

        t0 = time.perf_counter()
        async with _infer_lock:
            text = await asyncio.to_thread(
                _transcribe_sync, model, wav_path, language, initial_prompt
            )
        duration = time.perf_counter() - t0

        return {"text": text, "duration_seconds": duration}
    finally:
        for p in (src_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await prewarm()
    yield


app = FastAPI(title="beWithMe transcribe", lifespan=lifespan)
app.include_router(router, prefix="/api")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.transcribe.main:app",
        host="0.0.0.0",
        port=service_port("transcribe"),
        reload=False,
    )


if __name__ == "__main__":
    main()
