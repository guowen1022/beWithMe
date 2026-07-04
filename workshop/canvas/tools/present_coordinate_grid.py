"""present_coordinate_grid — teacher's tool for short animated Manim videos.

The sibling of the instant ``plot`` fence: plot = a static/interactive
Plotly chart the note renders client-side; this = a rendered *video* for
concepts that are inherently about motion or change — a grid warping
under a linear map, a point sweeping along a curve.

Pipeline: frozen spec (validated, expressions AST-whitelisted in
`_manim_scene`) → codegen a GridScene from a fixed template → render via
`python -m manim -ql` in a subprocess → mp4 lands in
``data/renders/<user_id>/<uuid>.mp4`` → mount a note whose markdown
carries a ```` ```skill:coordinate-grid ```` fence pointing at
``/api/renders/<name>`` (served by services/persona/routers/renders.py).
The coordinate-grid.js skill fetches the mp4 with the user's auth header
and injects a ``<video>`` — skills bypass the note sanitizer, which
allows ``<img>`` but not ``<video>``.

Manim is an optional dependency; when absent the tool returns a clear
error instead of crashing (see CLAUDE.md prerequisites).
"""
from __future__ import annotations

import json
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolDomain, ToolSpec
from infra.user_data import register_user_dir
from workshop.canvas.tools import _manim_scene
from workshop.canvas.tools.mount_template import mount_template

RENDERS_ROOT = register_user_dir(
    "canvas",
    Path(__file__).resolve().parents[3] / "data" / "renders",
    "Manim-rendered coordinate-grid videos (mp4) keyed by user.",
)


async def present_coordinate_grid(
    *, user_id: UUID, args: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate, render, and mount. Returns a result dict; all failure
    modes come back as ``{"error": ...}`` so the teacher's LLM can fix
    its spec and retry in the same turn."""
    try:
        spec = _manim_scene.normalize_spec(args)
    except ValueError as exc:
        return {"error": str(exc)}

    scene_source, video_seconds = _manim_scene.generate_scene(spec)
    name = f"{_uuid.uuid4().hex}.mp4"
    out_path = RENDERS_ROOT / str(user_id) / name
    try:
        render_seconds = await _manim_scene.render_scene(scene_source, out_path)
    except RuntimeError as exc:
        return {"error": str(exc)}

    video_url = f"/api/renders/{name}"
    fence_config = json.dumps({"video_url": video_url})
    title = spec["title"]
    heading = f"## {title}\n\n" if title else ""
    markdown = f"{heading}```skill:coordinate-grid\n{fence_config}\n```\n"

    try:
        result = await mount_template(
            user_id=user_id,
            template_name="note",
            block_id=None if title else "coordinate-grid",
            params={"markdown": markdown},
        )
    except ValueError as exc:
        return {"error": str(exc)}

    return {
        "block_id": result.block_id,
        "video_url": video_url,
        "video_seconds": video_seconds,
        "render_seconds": round(render_seconds, 1),
    }


def _make_executor(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        result = await present_coordinate_grid(user_id=user_id, args=args)
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="present_coordinate_grid",
        description=(
            "Render a short ANIMATED coordinate-grid VIDEO (Manim) and "
            "mount it on the canvas as a note. Use this when the concept "
            "is inherently about motion or change over time — a grid "
            "warping under a 2x2 linear transform, a point traveling "
            "along a curve, functions being drawn in sequence. For a "
            "quick static or interactive chart, embed a ```plot fence "
            "in a note instead (instant, no render wait); this tool "
            "takes several seconds to render. Functions are plain math "
            "expressions in x (e.g. 'x*x - 2', 'sin(2*x)', 'exp(-x)'); "
            "use ** for powers, never ^. No data scatter — analytic "
            "curves only."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short title shown above the animation; also names "
                        "the note block (e.g. 'Shear warps the grid')."
                    ),
                },
                "x_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[min, max] for x. Default [-5, 5].",
                },
                "y_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[min, max] for y. Default [-3, 3].",
                },
                "functions": {
                    "type": "array",
                    "description": (
                        "1-3 curves, drawn in order. Each: {expression, "
                        "color?, label?}. expression is math in x using "
                        "sin/cos/tan/sqrt/exp/log/abs etc. color is one "
                        f"of: {', '.join(sorted(_manim_scene.COLORS))}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string"},
                            "color": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["expression"],
                        "additionalProperties": False,
                    },
                },
                "transform": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                    "description": (
                        "Optional 2x2 matrix [[a,b],[c,d]]. Animates the "
                        "grid (and curves) warping under this linear map "
                        "— THE reason to pick video over plot."
                    ),
                },
                "moving_point": {
                    "type": "object",
                    "properties": {"on": {"type": "integer"}},
                    "additionalProperties": False,
                    "description": (
                        'Optional {"on": i}: a dot travels along the i-th '
                        "function's curve (0-based)."
                    ),
                },
                "duration": {
                    "type": "number",
                    "description": "Target video length in seconds, 4-20. Default 8.",
                },
            },
            "required": ["functions"],
            "additionalProperties": False,
        },
        executor=_make_executor(user_id),
        domain=ToolDomain.CANVAS,
    )


__all__ = ["RENDERS_ROOT", "present_coordinate_grid", "build_spec"]
