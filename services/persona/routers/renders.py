"""Serve Manim-rendered videos.

GET /api/renders/{name} → data/renders/<user_id>/<name>

The user comes from the auth header (never the path), so one user cannot
probe another's renders. Files are written by the
`present_coordinate_grid` canvas tool (workshop/canvas/tools/), which
owns the data/renders root; names are uuid4-hex mp4s, enforced here.

Lives on the persona sidecar because that's where the teacher's canvas
tools execute (shell routes the `renders` prefix here — infra/topology.py).
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from infra.auth import parse_user_id as get_current_user_id
from workshop.canvas.tools.present_coordinate_grid import RENDERS_ROOT

router = APIRouter()

_SAFE_NAME = re.compile(r"^[a-f0-9]{32}\.mp4$")


@router.get("/api/renders/{name}")
async def get_render(
    name: str, user_id: UUID = Depends(get_current_user_id)
) -> FileResponse:
    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="invalid render name")
    path = RENDERS_ROOT / str(user_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"render not found: {name}")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=86400"},
    )
