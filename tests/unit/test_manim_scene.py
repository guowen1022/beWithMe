"""Unit tests for the present_coordinate_grid codegen pipeline.

Everything except the last test runs without Manim installed — spec
validation, the expression AST whitelist, and codegen are pure string
work. The render test self-skips when manim is absent (it is an optional
dependency; see CLAUDE.md prerequisites).
"""
from __future__ import annotations

import ast
import asyncio

import pytest

from workshop.canvas.tools._manim_scene import (
    _float_source,
    _tick_decimals,
    compile_expression,
    generate_scene,
    manim_available,
    normalize_spec,
    render_scene,
)


# ------------------------------------------------------ expression whitelist

@pytest.mark.parametrize("expr", [
    "x*x",
    "sin(2*x) + 1",
    "-x**2 + 3",
    "exp(-x) * cos(3*x)",
    "log(abs(x) + 1)",
    "sqrt(x % 5) / 2",
    "pi * x - e",
    "floor(x) + ceil(x / 2)",
])
def test_expression_accepts_plain_math(expr):
    out = compile_expression(expr)
    # canonical form must itself re-validate (idempotent)
    assert compile_expression(out) == out


@pytest.mark.parametrize("expr", [
    "__import__('os').system('true')",   # call injection
    "x.__class__",                        # attribute access
    "().__class__",                       # dunder crawl
    "[i for i in (1,2)]",                 # comprehension
    "lambda y: y",                        # lambda
    "open('f')",                          # unknown call
    "'hello'",                            # string constant
    "x[0]",                               # subscript
    "y + 1",                              # unknown name
    "x > 1",                              # comparison
    "x and 1",                            # boolop
    "f'{x}'",                             # f-string
    "1e12 * x",                           # oversized constant
    "x := 3",                             # walrus (parse error in eval mode)
    "",                                   # empty
])
def test_expression_rejects_non_math(expr):
    with pytest.raises(ValueError):
        compile_expression(expr)


def test_expression_caret_gets_actionable_message():
    with pytest.raises(ValueError, match=r"x\*\*2"):
        compile_expression("x^2")


def test_expression_length_cap():
    with pytest.raises(ValueError, match="too long"):
        compile_expression("x + " * 100 + "x")


# ------------------------------------------------------- spec normalization

def _spec(**overrides):
    base = {
        "title": "Parabola",
        "functions": [{"expression": "x*x"}],
    }
    base.update(overrides)
    return base


def test_normalize_applies_defaults():
    spec = normalize_spec(_spec())
    assert spec["x_range"] == [-5.0, 5.0]
    assert spec["y_range"] == [-3.0, 3.0]
    assert spec["duration"] == 8.0
    assert spec["transform"] is None
    assert spec["moving_point"] is None
    fn = spec["functions"][0]
    assert fn["expression"] == "x * x"          # canonical unparse
    assert fn["color"] == "BLUE"                # first of the default cycle
    assert fn["label"] == "y = x*x"             # label spacing compressed


def test_normalize_rejects_bad_shapes():
    with pytest.raises(ValueError, match="functions"):
        normalize_spec({"title": "t"})
    with pytest.raises(ValueError, match="at most"):
        normalize_spec(_spec(functions=[{"expression": "x"}] * 4))
    with pytest.raises(ValueError, match="color"):
        normalize_spec(_spec(functions=[{"expression": "x", "color": "mauve"}]))
    with pytest.raises(ValueError, match="x_range"):
        normalize_spec(_spec(x_range=[0, 1000]))
    with pytest.raises(ValueError, match="2x2"):
        normalize_spec(_spec(transform=[[1, 0, 0], [0, 1, 0]]))
    with pytest.raises(ValueError, match="moving_point.on"):
        normalize_spec(_spec(moving_point={"on": 5}))


def test_normalize_clamps_duration():
    assert normalize_spec(_spec(duration=1))["duration"] == 4.0
    assert normalize_spec(_spec(duration=600))["duration"] == 20.0


def test_normalize_rejects_bool_where_int_expected():
    # bool is an int subclass; True must not sail through as index 1 (→ a
    # codegen NameError) or as a duration of 1.0s.
    with pytest.raises(ValueError, match="moving_point.on"):
        normalize_spec(_spec(
            functions=[{"expression": "x"}, {"expression": "x + 1"}],
            moving_point={"on": True},
        ))
    with pytest.raises(ValueError, match="duration"):
        normalize_spec(_spec(duration=True))


# --------------------------------------------------- bignum-DoS / float codegen

@pytest.mark.parametrize("step,dec", [
    (0.25, 2), (0.5, 1), (1.0, 0), (2.0, 0), (5.0, 0), (10.0, 0),
])
def test_tick_decimals_from_step_precision(step, dec):
    # a flat `0 if integer else 1` mislabels a 0.25 step as 0.2/0.5/0.8
    assert _tick_decimals(step) == dec


def test_quarter_step_axis_labels_two_decimals():
    # x span 2 → _nice_step picks 0.25 → ticks need 2 decimals to be exact
    source, _ = generate_scene(normalize_spec(_spec(x_range=[-1, 1])))
    assert "num_decimal_places': 2" in source


def test_render_lambda_uses_float_literals():
    # integer literals in the render lambda become floats; the label stays
    # integer-clean because it is built from the canonical form, not this one
    spec = normalize_spec(_spec(functions=[{"expression": "x*x - 2"}]))
    source, _ = generate_scene(spec)
    assert "_guard(lambda x: x * x - 2.0" in source
    assert spec["functions"][0]["label"] == "y = x*x-2"


@pytest.mark.parametrize("expr", [
    "999999**999999",          # bare composed power
    "2**(999999*999999)",      # power of a product
    "((10**12)**12)**12",      # nested powers
])
def test_composed_powers_overflow_instead_of_bignum(expr):
    # each literal clears the per-node cap (abs ≤ 1e6) but composes an
    # unbounded integer — a render-subprocess DoS. Float codegen turns that
    # into an O(1) OverflowError, which _guard catches.
    src = _float_source(compile_expression(expr))
    with pytest.raises(OverflowError):
        eval(src, {"__builtins__": {}}, {"x": 1.5})


# ------------------------------------------------------ duration as a ceiling

def test_duration_ceiling_speeds_up_long_animation():
    # fixed choreography here is ~10.9s; a 5s target must scale run_times
    # down (bounded at half-speed) instead of only trimming the wait.
    spec = normalize_spec({
        "x_range": [-4, 4], "y_range": [-2, 6],
        "functions": [{"expression": "x*x"}, {"expression": "sin(x)"},
                      {"expression": "x"}],
        "transform": [[1, 1], [0, 1]],
        "moving_point": {"on": 0},
        "duration": 5,
    })
    source, seconds = generate_scene(spec)
    compile(source, "<generated>", "exec")
    assert seconds <= 6.5                 # was ~11.9 before the fix
    assert "run_time=0.6)" in source      # curves sped 1.2 -> 0.6 (0.5x cap)


def test_duration_floor_pads_short_animation():
    # target above the fixed choreography: no speed-up, wait pads to target
    source, seconds = generate_scene(normalize_spec(_spec(duration=15)))
    assert seconds == 15.0
    assert "run_time=1.2)" in source      # curve unscaled (scale 1.0)


# ------------------------------------------------------------------ codegen

def _full_spec():
    return normalize_spec({
        "title": "Shear warps the grid",
        "x_range": [-4, 4],
        "y_range": [-2, 6],
        "functions": [
            {"expression": "x*x", "color": "yellow", "label": "y = x²"},
            {"expression": "sin(2*x)", "color": "teal"},
        ],
        "transform": [[1, 1], [0, 1]],
        "moving_point": {"on": 0},
        "duration": 12,
    })


def test_generate_scene_is_valid_python():
    source, seconds = generate_scene(_full_spec())
    compile(source, "<generated>", "exec")     # must parse as a module
    assert seconds >= 4.0


def test_generate_scene_structure():
    source, _ = generate_scene(_full_spec())
    assert "class GridScene(Scene):" in source
    assert source.count("axes.plot") == 2
    assert "ApplyMatrix" in source
    assert "MoveAlongPath" in source
    # expressions ride inside the guard, never bare eval
    assert "_guard(lambda x: x * x" in source
    # nothing beyond manim + math ever gets imported
    tree = ast.parse(source)
    imported = {
        n.module if isinstance(n, ast.ImportFrom) else n.names[0].name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert imported == {"manim", "math"}


def test_generate_scene_title_is_reprd():
    spec = normalize_spec(_spec(title='hi") import os #'))
    source, _ = generate_scene(spec)
    compile(source, "<generated>", "exec")
    assert "import os" not in source.replace(spec["title"], "")


def test_generate_scene_minimal_has_no_optional_parts():
    source, _ = generate_scene(normalize_spec(_spec()))
    assert "ApplyMatrix" not in source
    assert "MoveAlongPath" not in source
    assert "NumberPlane" not in source


# ------------------------------------------------------------------- render

@pytest.mark.skipif(not manim_available(), reason="manim not installed")
def test_render_scene_produces_mp4(tmp_path):
    source, _ = generate_scene(normalize_spec(_spec(duration=4)))
    out = tmp_path / "out.mp4"
    seconds = asyncio.run(render_scene(source, out, timeout_s=240))
    assert out.exists() and out.stat().st_size > 0
    assert seconds > 0
