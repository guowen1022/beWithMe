"""System-prompt assembly for the app_operator persona.

Kept deliberately tiny — the operating rules live in
`skills/app_operator.md` so they can be edited without touching code.
"""
from __future__ import annotations

from pathlib import Path

_SKILL = Path(__file__).parent / "skills" / "app_operator.md"


def build_system() -> str:
    base = (
        "You are app_operator, the part of beWithMe that performs app-level "
        "actions on the user's behalf."
    )
    try:
        skill = _SKILL.read_text(encoding="utf-8").strip()
    except OSError:
        skill = ""
    return base + ("\n\n" + skill if skill else "")


__all__ = ["build_system"]
