"""Serve frontend block skill files.

GET /api/skills/{name} → frontend/public/skills/<name>.js

Skill files are self-contained JavaScript snippets that extend the note
block's rendering capabilities (e.g. coordinate-plot.js renders Plotly
charts). The note block fetches them on demand via this endpoint.

Adding a new skill = drop a .js file in frontend/public/skills/ — no
code change needed.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILLS_DIR = _REPO_ROOT / "frontend" / "public" / "skills"

# Only allow safe filenames: lowercase alphanumeric + hyphens
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


@router.get("/api/skills/{name}")
async def get_skill(name: str) -> Response:
    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="invalid skill name")
    path = _SKILLS_DIR / f"{name}.js"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    js = path.read_text(encoding="utf-8")
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )
