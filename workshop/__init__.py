"""Cross-persona skill workshop.

A "skill set" is a coherent bundle of markdown skills + Python tools that
multiple personas/agents can pull from. The first set is `canvas` —
block primitives shared by the teacher and the frontend engineer.

Skills are addressed by qualified name `<set>/<name>`:

  workshop/canvas/grid          → workshop/canvas/skills/grid.md
  teacher/teaching_principle    → persona/teacher/skills/teaching_principle.md
  engineer/<name>               → agents/frontend_engineer/skills/<name>.md

The `<set>` prefix maps to a registered root directory (see `_ROOTS`
below). New sets register at import time.
"""
from __future__ import annotations

import functools
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_ROOTS: dict[str, Path] = {
    "workshop/canvas": _REPO_ROOT / "workshop" / "canvas" / "skills",
    "teacher": _REPO_ROOT / "persona" / "teacher" / "skills",
    "engineer": _REPO_ROOT / "agents" / "frontend_engineer" / "skills",
}


def register_root(prefix: str, path: Path) -> None:
    """Register a new skill-set root. Used by additional packages that
    want to participate in the workshop namespace."""
    _ROOTS[prefix] = path


@functools.lru_cache(maxsize=128)
def load_skill(qualified_name: str) -> str:
    """Load a markdown skill by qualified name.

    Splits on the last `/` — everything before is the set prefix, the
    last segment is the skill filename (without `.md`). Raises
    `KeyError` for an unregistered prefix; returns "" if the file
    doesn't exist (callers can decide whether absence is fatal).
    """
    if "/" not in qualified_name:
        raise ValueError(
            f"skill name must be qualified as '<set>/<name>', got {qualified_name!r}"
        )
    prefix, _, name = qualified_name.rpartition("/")
    root = _ROOTS.get(prefix)
    if root is None:
        raise KeyError(
            f"unknown skill-set prefix {prefix!r} for {qualified_name!r}; "
            f"known prefixes: {sorted(_ROOTS)}"
        )
    path = root / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


__all__ = ["load_skill", "register_root"]
