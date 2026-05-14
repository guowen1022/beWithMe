"""Transcribe sidecar — :BASE_PORT+3.

Local Whisper transcription via pywhispercpp. Loads a GGML model in lifespan
(best-effort) so the first /transcribe request is fast.

Also hosts /api/eou — the LiveKit text-only turn-detector model. Lives on the
same sidecar because (a) it's a small perception primitive on the hot voice
path, (b) keeping it co-located avoids a dependency edge from persona to a
new sidecar. ONNX runtime is loaded lazily; if EOU_MODEL_PATH is unset, the
endpoint returns 503 and clients fail open.

Run standalone:
    python -m services.transcribe
"""
from __future__ import annotations

import asyncio
import math
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from pydantic_settings import BaseSettings

from infra.contracts.transcribe import EouRequest, EouResponse
from infra.event_log import log_event
from infra.event_log_middleware import install_event_log
from infra.topology import service_port


# Whisper config is local to this sidecar — no other module needs these paths.
load_dotenv()


class TranscribeSettings(BaseSettings):
    # Local Whisper (pywhispercpp). Reuses Superwhisper's model by default.
    whisper_model_path: str = (
        "/Users/weng/Library/Application Support/Superwhisper/ggml-small.bin"
    )
    whisper_threads: int = 4

    # EOU (end-of-utterance) — LiveKit text turn-detector.
    # Both paths unset → endpoint returns 503; client gates fail open
    # (behavior matches today, no disfluency handling).
    eou_model_path: str = ""
    eou_tokenizer_path: str = ""
    eou_threshold: float = 0.55
    # Max tokens fed to the model. The turn-detector only cares about the
    # tail of the user turn — clip the head to keep inference cheap.
    eou_max_tokens: int = 256

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

# EOU session — separate lifecycle from Whisper. onnxruntime InferenceSession
# also isn't safe under concurrent run() across threads — same lock pattern.
_eou_session = None
_eou_tokenizer = None
_eou_model_lock = asyncio.Lock()
_eou_infer_lock = asyncio.Lock()


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

    # EOU is optional — only warm if both paths are set. A missing model
    # is the normal "feature disabled" state, not an error.
    if settings.eou_model_path and settings.eou_tokenizer_path:
        try:
            await _get_eou()
            print(f"[transcribe] eou model warmed: {settings.eou_model_path}", flush=True)
        except HTTPException as err:
            print(f"[transcribe] eou prewarm skipped: {err.detail}", flush=True)
        except Exception as err:  # noqa: BLE001
            print(f"[transcribe] eou prewarm failed: {err}", flush=True)


# --- EOU (end-of-utterance) — LiveKit text turn-detector ---------------------
#
# Input format mirrors the model card: a chat-template-rendered conversation
# whose final turn is the user's in-progress utterance. The model is a small
# decoder-only transformer with a binary classification head; the EOT class
# index is whichever the model card specifies (LiveKit's published model uses
# index 1). The exact template lives in the tokenizer's chat_template field —
# we render it via `tokenizers.Tokenizer.apply_chat_template` (or fall back
# to a manual format if the tokenizer doesn't carry one).
#
# If you swap models, the only place that needs to change is _format_eou_input
# below + EOT_CLASS_INDEX.

_EOU_EOT_CLASS_INDEX = 1


def _load_eou():
    """Blocking load of the ONNX session + tokenizer.

    Returns (session, tokenizer) or raises. Call inside asyncio.to_thread.
    """
    import onnxruntime as ort  # type: ignore
    from tokenizers import Tokenizer  # type: ignore

    session = ort.InferenceSession(
        settings.eou_model_path,
        providers=["CPUExecutionProvider"],
    )
    # Tokenizer dir is a HF-style directory; the JSON file is what we need.
    tok_path = settings.eou_tokenizer_path
    if os.path.isdir(tok_path):
        tok_file = os.path.join(tok_path, "tokenizer.json")
    else:
        tok_file = tok_path
    if not os.path.isfile(tok_file):
        raise FileNotFoundError(f"tokenizer.json not found at {tok_file!r}")
    tokenizer = Tokenizer.from_file(tok_file)
    return session, tokenizer


async def _get_eou():
    """Lazy-load the EOU session + tokenizer. Raises 503 if paths unset."""
    global _eou_session, _eou_tokenizer
    if _eou_session is not None and _eou_tokenizer is not None:
        return _eou_session, _eou_tokenizer
    if not settings.eou_model_path or not settings.eou_tokenizer_path:
        raise HTTPException(
            status_code=503,
            detail=(
                "EOU model not configured. Set EOU_MODEL_PATH and "
                "EOU_TOKENIZER_PATH in .env, then run "
                "scripts/fetch_eou_model.sh for instructions."
            ),
        )
    async with _eou_model_lock:
        if _eou_session is not None and _eou_tokenizer is not None:
            return _eou_session, _eou_tokenizer
        if not os.path.isfile(settings.eou_model_path):
            raise HTTPException(
                status_code=503,
                detail=f"EOU model file missing at {settings.eou_model_path!r}",
            )
        try:
            _eou_session, _eou_tokenizer = await asyncio.to_thread(_load_eou)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"EOU model load failed: {err}")
        return _eou_session, _eou_tokenizer


def _format_eou_input(prior_turns: list[dict], user_text: str) -> str:
    """Render the model input. Manual template — mirrors the LiveKit
    turn-detector card. If the tokenizer ships a chat_template, callers
    can swap in tokenizer.apply_chat_template; the manual form keeps us
    independent of tokenizer-version drift.

    Format:
        <|im_start|>user
        {text}<|im_end|>
        <|im_start|>assistant
        {text}<|im_end|>
        ...
        <|im_start|>user
        {current_user_text}                       <-- no closing tag, no EOS
    """
    parts = []
    for t in prior_turns:
        role = t.get("role", "user")
        text = (t.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"<|im_start|>{role}\n{text}<|im_end|>")
    parts.append(f"<|im_start|>user\n{user_text}")
    return "\n".join(parts)


def _eou_infer_sync(session, tokenizer, text: str, max_tokens: int) -> float:
    """Run the model and return P(end_of_turn). Tail-truncates the input
    so a long context stays within max_tokens."""
    import numpy as np  # type: ignore

    enc = tokenizer.encode(text)
    ids = enc.ids
    if len(ids) > max_tokens:
        ids = ids[-max_tokens:]
    input_ids = np.asarray([ids], dtype=np.int64)
    attention_mask = np.ones_like(input_ids, dtype=np.int64)

    # The model's input/output names vary by export. Probe the session
    # to be tolerant of small differences (input_ids vs inputs_ids,
    # attention_mask optional).
    feed = {}
    input_names = {i.name for i in session.get_inputs()}
    if "input_ids" in input_names:
        feed["input_ids"] = input_ids
    elif "inputs" in input_names:
        feed["inputs"] = input_ids
    else:
        feed[next(iter(input_names))] = input_ids
    if "attention_mask" in input_names:
        feed["attention_mask"] = attention_mask

    outputs = session.run(None, feed)
    logits = outputs[0]
    # Expect shape (1, 2) for a binary classifier. If the model returns
    # per-token logits, take the last token.
    if logits.ndim == 3:
        logits = logits[:, -1, :]
    row = logits[0]
    # Stable softmax.
    m = float(row.max())
    exps = [math.exp(float(x) - m) for x in row]
    z = sum(exps)
    probs = [e / z for e in exps]
    idx = min(_EOU_EOT_CLASS_INDEX, len(probs) - 1)
    return float(probs[idx])


@router.post("/eou", response_model=EouResponse)
async def eou(body: EouRequest) -> EouResponse:
    session, tokenizer = await _get_eou()
    threshold = body.threshold if body.threshold is not None else settings.eou_threshold

    user_text = " ".join(t.strip() for t in body.transcripts if t and t.strip())
    if not user_text:
        # Nothing to score — treat as "not done" so the client keeps listening.
        return EouResponse(end_prob=0.0, end_of_turn=False, threshold=threshold, infer_ms=0.0)

    prior = [{"role": t.role, "text": t.text} for t in body.prior_turns]
    text = _format_eou_input(prior, user_text)

    t0 = time.perf_counter()
    async with _eou_infer_lock:
        prob = await asyncio.to_thread(
            _eou_infer_sync, session, tokenizer, text, settings.eou_max_tokens,
        )
    infer_ms = round((time.perf_counter() - t0) * 1000, 2)

    log_event(
        "eou.score",
        text_len=len(user_text),
        phrases=len(body.transcripts),
        prior_turns=len(body.prior_turns),
        prob=round(prob, 4),
        threshold=threshold,
        infer_ms=infer_ms,
    )
    return EouResponse(
        end_prob=prob,
        end_of_turn=prob >= threshold,
        threshold=threshold,
        infer_ms=infer_ms,
    )


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

        decode_t0 = time.perf_counter()
        await asyncio.to_thread(_decode_to_wav, src_path, wav_path)
        decode_ms = round((time.perf_counter() - decode_t0) * 1000, 2)

        t0 = time.perf_counter()
        async with _infer_lock:
            text = await asyncio.to_thread(
                _transcribe_sync, model, wav_path, language, initial_prompt
            )
        duration = time.perf_counter() - t0

        log_event(
            "transcribe.done",
            language=language,
            audio_bytes=len(data),
            text_len=len(text),
            decode_ms=decode_ms,
            whisper_ms=round(duration * 1000, 2),
        )
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
install_event_log(app, service="transcribe")
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
