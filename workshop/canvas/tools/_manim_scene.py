"""_manim_scene — spec → Manim scene codegen + subprocess renderer for
`present_coordinate_grid`.

Sandbox stance (see brainstorm/tool-refining/10-tool-contract-sandbox.md):
the LLM never writes Python. It fills a frozen spec (title, ranges, up to
three function expressions, an optional 2×2 transform, an optional moving
point, a duration) and we codegen the scene from a fixed template. The
only model-authored text that becomes code is the math expressions, and
those pass a strict AST whitelist first — arithmetic operators, calls to
a fixed set of math functions, the name `x`, and the constants `pi`/`e`.
The validated tree is re-serialized with `ast.unparse`, so what lands in
the generated file is exactly the whitelisted AST, nothing else. String
slots (title, labels) are embedded via `repr()`. Defense in depth: the
render runs in a subprocess with a hard timeout.

Rendering needs Manim CE (optional dep — NOT in requirements.txt):
    brew install cairo pango && .venv/bin/python -m pip install manim
No LaTeX required: the template uses `Text` everywhere (axis tick numbers
via `label_constructor=Text`, verified against manim 0.20).
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import math
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- spec caps

MAX_FUNCTIONS = 3
EXPR_MAX_CHARS = 120
EXPR_MAX_NODES = 80
LABEL_MAX_CHARS = 40
TITLE_MAX_CHARS = 80
RANGE_ABS_MAX = 100.0
RANGE_MIN_SPAN = 0.5
MATRIX_ABS_MAX = 10.0
DURATION_MIN_S = 4.0
DURATION_MAX_S = 20.0
DURATION_DEFAULT_S = 8.0

RENDER_TIMEOUT_S = 180.0

# Curve colors the teacher may pick; cycle order doubles as the default.
COLORS: Dict[str, str] = {
    "blue": "BLUE",
    "yellow": "YELLOW",
    "green": "GREEN",
    "red": "RED",
    "orange": "ORANGE",
    "purple": "PURPLE",
    "teal": "TEAL",
    "pink": "PINK",
    "white": "WHITE",
}
_COLOR_CYCLE = ["blue", "yellow", "green"]

# ------------------------------------------------- expression AST whitelist

_ALLOWED_FUNCS = frozenset({
    "sin", "cos", "tan", "asin", "acos", "atan",
    "sinh", "cosh", "tanh",
    "sqrt", "exp", "log", "abs", "floor", "ceil",
})
_ALLOWED_NAMES = frozenset({"x", "pi", "e"})
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def compile_expression(expr: str) -> str:
    """Validate `expr` as a pure f(x) math expression and return its
    canonical `ast.unparse` form. Raises ValueError with a message the
    teacher's LLM can act on (it becomes the tool error verbatim)."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("expression must be a non-empty string")
    expr = expr.strip()
    if len(expr) > EXPR_MAX_CHARS:
        raise ValueError(f"expression too long (max {EXPR_MAX_CHARS} chars)")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"expression does not parse: {exc.msg}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > EXPR_MAX_NODES:
        raise ValueError("expression too complex")

    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.BitXor):
                raise ValueError("'^' is not power — write x**2, not x^2")
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise ValueError(f"operator {type(node.op).__name__} not allowed")
            continue
        if isinstance(node, _ALLOWED_BINOPS + _ALLOWED_UNARYOPS):
            continue
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise ValueError(f"operator {type(node.op).__name__} not allowed")
            continue
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name)
                    or node.func.id not in _ALLOWED_FUNCS
                    or node.keywords):
                raise ValueError(
                    "only these functions are allowed: "
                    + ", ".join(sorted(_ALLOWED_FUNCS))
                )
            continue
        if isinstance(node, ast.Name):
            if node.id not in _ALLOWED_NAMES and node.id not in _ALLOWED_FUNCS:
                raise ValueError(
                    f"unknown name {node.id!r} — the only variable is x "
                    "(constants: pi, e)"
                )
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("only numeric constants are allowed")
            if abs(node.value) > 1e6:
                raise ValueError("numeric constant too large")
            continue
        raise ValueError(
            f"{type(node).__name__} is not allowed — expressions are plain "
            "math in x, e.g. 'x*x - 2' or 'sin(2*x)'"
        )

    return ast.unparse(tree)


# ------------------------------------------------------- spec normalization

def _norm_range(value: Any, name: str, default: List[float]) -> List[float]:
    if value is None:
        return list(default)
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) for v in value)):
        raise ValueError(f"{name} must be [min, max]")
    lo, hi = float(value[0]), float(value[1])
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(f"{name} must be finite")
    if max(abs(lo), abs(hi)) > RANGE_ABS_MAX:
        raise ValueError(f"{name} values must be within ±{RANGE_ABS_MAX:g}")
    if hi - lo < RANGE_MIN_SPAN:
        raise ValueError(f"{name} must span at least {RANGE_MIN_SPAN:g}")
    return [lo, hi]


def normalize_spec(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + default the tool args into a render-ready spec.
    Raises ValueError; the tool converts that into `{"error": ...}`."""
    title = args.get("title") or ""
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    title = title.strip()[:TITLE_MAX_CHARS]

    x_range = _norm_range(args.get("x_range"), "x_range", [-5.0, 5.0])
    y_range = _norm_range(args.get("y_range"), "y_range", [-3.0, 3.0])

    raw_fns = args.get("functions")
    if not isinstance(raw_fns, list) or not raw_fns:
        raise ValueError("functions must be a non-empty list")
    if len(raw_fns) > MAX_FUNCTIONS:
        raise ValueError(f"at most {MAX_FUNCTIONS} functions")
    functions = []
    for i, fn in enumerate(raw_fns):
        if not isinstance(fn, dict):
            raise ValueError("each function must be an object")
        expression = compile_expression(fn.get("expression"))
        color = fn.get("color") or _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
        if color not in COLORS:
            raise ValueError(
                f"color must be one of: {', '.join(sorted(COLORS))}"
            )
        label = fn.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        # default label from the canonical expression, minus unparse's
        # `x * x`-style spacing — labels read as math, not code
        label = (label or f"y = {expression.replace(' ', '')}").strip()[:LABEL_MAX_CHARS]
        functions.append(
            {"expression": expression, "color": COLORS[color], "label": label}
        )

    transform = args.get("transform")
    if transform is not None:
        if (not isinstance(transform, (list, tuple)) or len(transform) != 2
                or not all(isinstance(row, (list, tuple)) and len(row) == 2
                           for row in transform)):
            raise ValueError("transform must be a 2x2 matrix [[a,b],[c,d]]")
        flat = [v for row in transform for v in row]
        if not all(isinstance(v, (int, float)) and math.isfinite(v)
                   and abs(v) <= MATRIX_ABS_MAX for v in flat):
            raise ValueError(
                f"transform entries must be numbers within ±{MATRIX_ABS_MAX:g}"
            )
        transform = [[float(v) for v in row] for row in transform]

    moving_point = args.get("moving_point")
    if moving_point is not None:
        if not isinstance(moving_point, dict):
            raise ValueError('moving_point must be an object like {"on": 0}')
        on = moving_point.get("on", 0)
        # bool is an int subclass — True would sail through as index 1 and
        # codegen `curveTrue` → NameError after a wasted render subprocess.
        if (isinstance(on, bool) or not isinstance(on, int)
                or not (0 <= on < len(functions))):
            raise ValueError(
                f"moving_point.on must be a function index 0..{len(functions) - 1}"
            )
        moving_point = {"on": on}

    duration = args.get("duration", DURATION_DEFAULT_S)
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration)):
        raise ValueError("duration must be a number of seconds")
    duration = min(max(float(duration), DURATION_MIN_S), DURATION_MAX_S)

    return {
        "title": title,
        "x_range": x_range,
        "y_range": y_range,
        "functions": functions,
        "transform": transform,
        "moving_point": moving_point,
        "duration": duration,
    }


# ----------------------------------------------------------------- codegen

_SCENE_HEADER = '''\
from manim import *
import math
from math import (
    sin, cos, tan, asin, acos, atan, sinh, cosh, tanh,
    sqrt, exp, log, floor, ceil, pi, e,
)


def _guard(f, lo, hi):
    """Keep model-authored f(x) render-safe: domain errors and non-finite
    values clamp to the plotted y-range instead of killing the render."""
    def g(x):
        try:
            y = float(f(x))
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            return lo
        if not math.isfinite(y):
            return lo
        return min(max(y, lo), hi)
    return g


class GridScene(Scene):
    def construct(self):
'''


def _nice_step(span: float) -> float:
    """Largest 'round' tick step giving at most ~12 ticks over `span`."""
    for step in (0.25, 0.5, 1, 2, 5, 10, 20, 50):
        if span / step <= 12:
            return float(step)
    return 50.0


def _tick_decimals(step: float) -> int:
    """Decimal places needed to label `step`'s ticks exactly: 0.25 -> 2,
    0.5 -> 1, integer steps -> 0. Derived from the step itself so any new
    `_nice_step` candidate stays correct (a flat `0 if integer else 1`
    mislabels 0.25 as 0.2/0.5/0.8)."""
    step = round(step, 6)
    if step == int(step):
        return 0
    return len(f"{step:.6f}".rstrip("0").partition(".")[2])


class _FloatConstants(ast.NodeTransformer):
    """Rewrite every integer literal to a float. `x**2` stays exact, but the
    emitted lambda then does all arithmetic in float space."""
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return ast.copy_location(ast.Constant(float(node.value)), node)
        return node


def _float_source(expr: str) -> str:
    """Re-emit an already-whitelisted expression with float literals.

    The per-node constant cap (abs <= 1e6) bounds each literal but not
    composition: whitelisted `**`/`*` chains (`999999**999999`,
    `2**(999999*999999)`, nested `((10**12)**12)...`) build unbounded
    *integer* results — a RAM/CPU DoS in the render subprocess, behind the
    Semaphore(1) that queues every other render. In float space those same
    expressions raise OverflowError or yield inf in O(1), and `_guard`
    already catches both. Labels are built from the canonical (int) form,
    so this stays confined to the render lambda."""
    tree = _FloatConstants().visit(ast.parse(expr, mode="eval"))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


# Per-segment animation run-times (seconds). The trailing self.wait() soaks
# up any leftover duration budget; everything else is fixed choreography.
_RT_TITLE = 0.5
_RT_PLANE = 1.0
_RT_AXES = 1.0
_RT_CURVE = 1.2
_RT_POINT_IN = 0.3
_RT_POINT_MOVE = 2.5
_RT_TRANSFORM = 2.5

# `duration` is a ceiling: when the fixed choreography runs longer than the
# target, scale run-times down to fit — but never below half-speed, so a
# tight budget can't compress the animation into an unwatchable blur.
_MIN_SPEEDUP_SCALE = 0.5


def _fixed_choreography_seconds(spec: Dict[str, Any]) -> float:
    """Total run-time of the fixed animation (everything but the trailing
    wait). The speed-up scale is derived from this so `duration` becomes a
    real ceiling instead of only trimming the final hold."""
    t = _RT_AXES + _RT_CURVE * len(spec["functions"])
    if spec["title"]:
        t += _RT_TITLE
    if spec["moving_point"] is not None:
        t += _RT_POINT_IN + _RT_POINT_MOVE
    if spec["transform"] is not None:
        t += _RT_PLANE + _RT_TRANSFORM
    return t


def generate_scene(spec: Dict[str, Any]) -> tuple[str, float]:
    """Codegen a complete Manim scene file from a normalized spec.
    Returns (source, estimated_seconds). Deterministic — the spec's
    expressions are already canonical `ast.unparse` output."""
    x0, x1 = spec["x_range"]
    y0, y1 = spec["y_range"]
    xstep = _nice_step(x1 - x0)
    ystep = _nice_step(y1 - y0)
    # decimals from the step's actual precision, else labels are wrong:
    # 0-decimals duplicates a 0.5 step (2 2 1 1 0 0 ...) and 1-decimal
    # mislabels a 0.25 step (0.2 0.5 0.8 instead of 0.25 0.5 0.75).
    xdec = _tick_decimals(xstep)
    ydec = _tick_decimals(ystep)

    # `duration` as a ceiling: if the fixed choreography is longer than the
    # target, speed every play() up proportionally (bounded at half-speed);
    # otherwise scale=1.0 and the trailing wait() pads up to the target.
    target = spec["duration"]
    fixed = _fixed_choreography_seconds(spec)
    scale = max(target / fixed, _MIN_SPEEDUP_SCALE) if fixed > target else 1.0

    def rt(base: float) -> float:
        return round(base * scale, 2)

    body: List[str] = []
    used = 0.0

    if spec["title"]:
        body += [
            f"title = Text({spec['title']!r}, font_size=30)",
            "title.to_edge(UP, buff=0.25)",
            f"self.play(FadeIn(title), run_time={rt(_RT_TITLE)!r})",
        ]
        used += rt(_RT_TITLE)

    axes_kwargs = (
        f"x_range=[{x0!r}, {x1!r}, {xstep!r}], "
        f"y_range=[{y0!r}, {y1!r}, {ystep!r}], "
        "x_length=11, y_length=5.5, tips=False, "
        "axis_config={'include_numbers': True, 'label_constructor': Text, "
        "'font_size': 18}, "
        f"x_axis_config={{'decimal_number_config': {{'num_decimal_places': {xdec}}}}}, "
        f"y_axis_config={{'decimal_number_config': {{'num_decimal_places': {ydec}}}}}"
    )
    body += [
        f"axes = Axes({axes_kwargs})",
        "axes.shift(DOWN * 0.3)",
    ]

    if spec["transform"] is not None:
        # The plane is what visibly warps under the matrix; the axes stay
        # put as the fixed frame of reference.
        body += [
            f"plane = NumberPlane(x_range=[{x0!r}, {x1!r}, {xstep!r}], "
            f"y_range=[{y0!r}, {y1!r}, {ystep!r}], "
            "x_length=11, y_length=5.5, "
            "background_line_style={'stroke_opacity': 0.35})",
            "plane.shift(DOWN * 0.3)",
            f"self.play(Create(plane), run_time={rt(_RT_PLANE)!r})",
        ]
        used += rt(_RT_PLANE)

    body.append(f"self.play(Create(axes), run_time={rt(_RT_AXES)!r})")
    used += rt(_RT_AXES)

    for i, fn in enumerate(spec["functions"]):
        body += [
            f"f{i} = _guard(lambda x: {_float_source(fn['expression'])}, {y0!r}, {y1!r})",
            f"curve{i} = axes.plot(f{i}, x_range=[{x0!r}, {x1!r}], "
            f"color={fn['color']}, use_smoothing=False)",
            f"label{i} = Text({fn['label']!r}, font_size=20, color={fn['color']})",
            f"label{i}.to_corner(UR, buff=0.3).shift(DOWN * {i * 0.45!r})",
            f"self.play(Create(curve{i}), FadeIn(label{i}), run_time={rt(_RT_CURVE)!r})",
        ]
        used += rt(_RT_CURVE)

    if spec["moving_point"] is not None:
        on = spec["moving_point"]["on"]
        body += [
            f"dot = Dot(color={spec['functions'][on]['color']}, radius=0.07)",
            f"dot.move_to(curve{on}.get_start())",
            f"self.play(FadeIn(dot), run_time={rt(_RT_POINT_IN)!r})",
            f"self.play(MoveAlongPath(dot, curve{on}), run_time={rt(_RT_POINT_MOVE)!r}, "
            "rate_func=linear)",
        ]
        used += rt(_RT_POINT_IN) + rt(_RT_POINT_MOVE)

    if spec["transform"] is not None:
        (a, b), (c, d) = spec["transform"]
        warped = "plane, " + ", ".join(
            f"curve{i}" for i in range(len(spec["functions"]))
        )
        if spec["moving_point"] is not None:
            warped += ", dot"  # the dot lives in the warped space too
        body += [
            f"warped = VGroup({warped})",
            f"self.play(ApplyMatrix([[{a!r}, {b!r}], [{c!r}, {d!r}]], warped, "
            f"about_point=axes.c2p(0, 0)), run_time={rt(_RT_TRANSFORM)!r})",
        ]
        used += rt(_RT_TRANSFORM)

    # Speed-up (scale<1) lands `used` near the target; otherwise the wait
    # pads up to it. The final hold keeps its 0.5s floor either way.
    wait = max(0.5, target - used)
    body.append(f"self.wait({round(wait, 2)!r})")

    source = _SCENE_HEADER + "".join(f"        {line}\n" for line in body)
    return source, round(used + wait, 1)


# ------------------------------------------------------------------ runner

def manim_available() -> bool:
    return importlib.util.find_spec("manim") is not None


# Manim renders are CPU-heavy (~20s each at -ql); serialize them so
# concurrent tool calls queue instead of thrashing the machine.
_render_lock = asyncio.Semaphore(1)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the render subprocess *and its child tree* (manim spawns ffmpeg),
    then reap it. Called on both timeout and cancellation so an aborted turn
    can't leave orphaned renders, race TemporaryDirectory cleanup against live
    children, or release the semaphore around a still-running process."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


async def render_scene(
    scene_source: str,
    out_path: Path,
    *,
    timeout_s: float = RENDER_TIMEOUT_S,
) -> float:
    """Render a GridScene source file to `out_path` (mp4) via
    `python -m manim -ql` in a temp dir. Returns wall-clock seconds.
    Raises RuntimeError with a stderr tail on any failure."""
    if not manim_available():
        raise RuntimeError(
            "manim is not installed — `brew install cairo pango` then "
            "`.venv/bin/python -m pip install manim`"
        )
    started = time.monotonic()
    async with _render_lock:
        with tempfile.TemporaryDirectory(prefix="bwm-manim-") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "scene.py").write_text(scene_source, encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "manim", "render",
                "-ql", "-v", "WARNING",
                "--media_dir", str(tmp_path / "media"),
                "--disable_caching", "--progress_bar", "none",
                "-o", "out", "scene.py", "GridScene",
                cwd=tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group, so we can killpg the tree
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                await _terminate(proc)
                raise RuntimeError(
                    f"manim render timed out after {timeout_s:.0f}s"
                )
            except BaseException:
                # Cancellation (SSE disconnect / turn abort propagates
                # CancelledError) or any other error while awaiting: tear the
                # subprocess down before we unwind out of the temp dir.
                await _terminate(proc)
                raise
            if proc.returncode != 0:
                tail = (stderr or stdout or b"").decode(
                    "utf-8", errors="replace"
                ).strip()[-500:]
                raise RuntimeError(f"manim render failed: {tail}")

            expected = tmp_path / "media" / "videos" / "scene" / "480p15" / "out.mp4"
            if not expected.exists():
                candidates = [
                    p for p in (tmp_path / "media").rglob("*.mp4")
                    if "partial_movie_files" not in p.parts
                ]
                if not candidates:
                    raise RuntimeError("manim produced no mp4 output")
                expected = candidates[0]
            if expected.stat().st_size == 0:
                raise RuntimeError("manim produced an empty mp4")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(expected), str(out_path))
    return time.monotonic() - started


__all__ = [
    "COLORS",
    "MAX_FUNCTIONS",
    "compile_expression",
    "generate_scene",
    "manim_available",
    "normalize_spec",
    "render_scene",
]
