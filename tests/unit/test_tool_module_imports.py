"""Guard: every tool module that uses ``json.*`` must ``import json``.

Regression test for architecture-review finding **F8** (2026-06-26): 16 ToolSpec
executors under ``tools/`` and ``workshop/canvas/tools/`` called
``json.dumps``/``json.loads`` with no ``import json``. Because the executor body
only runs when the persona LLM selects the tool — and ``agent_loop`` wraps every
executor in a blanket ``except Exception`` (``infra/model/agent_loop.py``) — the
``NameError`` was laundered into a ``{"error": ...}`` string and never surfaced
in unit/e2e runs. The root cause was a testability gap: nothing imported these
modules and checked them.

This static AST check closes that gap cheaply (no side effects, no topology):
if a tool module references ``json.<attr>`` anywhere, it must import ``json``.
It is parametrized one-test-per-module so a regression names the offending file.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOL_DIRS = [_ROOT / "tools", _ROOT / "workshop" / "canvas" / "tools"]


def _tool_modules() -> list[Path]:
    mods: list[Path] = []
    for d in _TOOL_DIRS:
        mods.extend(p for p in sorted(d.glob("*.py")) if p.name != "__init__.py")
    return mods


def _uses_json_attribute(tree: ast.AST) -> bool:
    """True if the module references ``json.<something>`` as a bare name."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "json"
        ):
            return True
    return False


def _imports_json(tree: ast.AST) -> bool:
    """True if ``json`` is imported anywhere (module- or function-level)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "json" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "json":
            return True
    return False


@pytest.mark.parametrize(
    "path", _tool_modules(), ids=lambda p: str(p.relative_to(_ROOT))
)
def test_tool_module_imports_json_if_used(path: Path) -> None:
    # Explicit encoding: bare read_text() uses the locale default, which on a
    # zh-CN Windows box is GBK and blows up on the UTF-8 punctuation these
    # sources contain. Linux CI defaults to UTF-8 and never sees it.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if _uses_json_attribute(tree):
        assert _imports_json(tree), (
            f"{path.relative_to(_ROOT)} uses json.* in an executor but does not "
            f"`import json` — this raises NameError at tool-call time (F8)."
        )
