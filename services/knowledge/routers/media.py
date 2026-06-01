"""Media upload — `POST /api/media/upload`.

Generalization of /documents/upload to non-PDF media. PDFs still flow through
the documents pipeline (text extraction → chunks → embeddings) because they
have *content* the persona reasons over. Media (video/audio/image) is just
*pixels* and *waveforms* — there's nothing to embed and chunk — so we just
persist the bytes to disk and hand the server-side path back so tools like
`look_at_video` and `look_at_image` can read it.

Files land under `data/uploads/<user_id>/<uuid><ext>` (repo-relative,
gitignored). One UUID per file, original extension preserved so ffmpeg can
demux without sniffing.
"""
from __future__ import annotations

import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from infra.auth import parse_user_id as get_current_user_id
from infra.user_data import register_user_dir


router = APIRouter()

_UPLOADS_ROOT = register_user_dir(
    "knowledge",
    Path(__file__).resolve().parents[3] / "data" / "uploads",
    "Raw uploaded media bytes (video / audio / image) keyed by user.",
)


_LOG_PATH = Path(__file__).resolve().parents[3] / ".dev-logs" / "media_upload.log"


def _log_exception(label: str, err: BaseException) -> None:
    """Capture full traceback to .dev-logs/media_upload.log AND stderr."""
    tb = traceback.format_exception(type(err), err, err.__traceback__)
    msg = f"[media_upload:{label}] " + "".join(tb)
    print(msg, file=sys.stderr, flush=True)
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a") as f:
            f.write(msg)
    except OSError:
        pass


_VIDEO_EXTS: Final = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
_AUDIO_EXTS: Final = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".flac", ".aac"}
_IMAGE_EXTS: Final = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

_KIND_BY_EXT = {
    **{ext: "video" for ext in _VIDEO_EXTS},
    **{ext: "audio" for ext in _AUDIO_EXTS},
    **{ext: "image" for ext in _IMAGE_EXTS},
}

_MAX_BYTES: Final = 500 * 1024 * 1024  # 500 MB per upload


def _uploads_root() -> Path:
    """Absolute path to <repo_root>/data/uploads/, created if missing."""
    _UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    return _UPLOADS_ROOT


@router.post("/media/upload")
async def upload_media(
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
):
    """Persist a media file and return the server-side path."""
    try:
        form = await request.form(max_part_size=_MAX_BYTES)
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status_code=400, detail="No file uploaded")
        filename = file.filename or ""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _KIND_BY_EXT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file extension {ext!r}. Supported: "
                    f"{sorted(_KIND_BY_EXT.keys())}"
                ),
            )
        media_kind = _KIND_BY_EXT[ext]

        data = await file.read()
        if len(data) > _MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({len(data)} bytes; max {_MAX_BYTES})",
            )
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")

        user_dir = _uploads_root() / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{uuid.uuid4().hex}{ext}"
        out_path = user_dir / out_name
        out_path.write_bytes(data)

        return {
            "path": str(out_path),
            "media_kind": media_kind,
            "filename": filename,
            "size": len(data),
        }
    except HTTPException:
        raise
    except Exception as e:
        _log_exception("upload", e)
        raise HTTPException(
            status_code=500,
            detail=f"media upload failed: {type(e).__name__}: {e}",
        )
