"""LEARNER PREFERENCES + USER BACKGROUND blocks for the system prompt.

Both go into the cacheable `static_system` since they only change when
the preference distiller re-runs (rare).
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from persona.teacher.preferences.state import UserProfileState


_STYLE_LABELS = {
    "explanation_style": "Explanation style",
    "depth_preference": "Depth",
    "analogy_affinity": "Use of analogies",
    "math_comfort": "Math comfort level",
    "pacing": "Pacing",
}


def render(
    user_profile: Optional["UserProfileState"],
    self_description: str = "",
) -> List[str]:
    """Return a list of system_parts lines (joined by the caller with
    `\\n`). Empty list when there's nothing to add.
    """
    out: List[str] = []
    if user_profile:
        pref_lines: List[str] = []
        for key, label in _STYLE_LABELS.items():
            val = getattr(user_profile, key, None)
            if val and val not in ("moderate", "balanced"):
                pref_lines.append(f"- {label}: {val}")
        if user_profile.meta_notes:
            pref_lines.append(f"- Notes: {user_profile.meta_notes}")
        if pref_lines:
            out.append("")
            out.append("LEARNER PREFERENCES:")
            out.extend(pref_lines)
    if self_description:
        out.append(f"\nUSER BACKGROUND:\n{self_description}")
    return out


_VALID_CHANNELS = {"voice", "text", "both"}
_DEFAULT_TALK_PREF: dict[str, str] = {
    "desktop": "both",
    "tablet": "both",
    "phone": "text",
}


def render_talk_preference(talk_preference: dict | None) -> List[str]:
    """Return system_parts lines describing how the teacher should
    deliver `speak()` utterances.

    `talk_preference` is `{desktop, tablet, phone}`, each value
    `voice` | `text` | `both`. The user's saved per-device choice is
    rendered as a deterministic rule the LLM applies by reading the
    active device class from CURRENTLY ON CANVAS — no natural-language
    interpretation required.
    """
    pref = dict(_DEFAULT_TALK_PREF)
    if isinstance(talk_preference, dict):
        for k in ("desktop", "tablet", "phone"):
            v = talk_preference.get(k)
            if isinstance(v, str) and v in _VALID_CHANNELS:
                pref[k] = v
    return [
        "",
        "TALK CHANNEL RULE — apply on every `speak` call:",
        f"  * desktop → channel='{pref['desktop']}'",
        f"  * tablet  → channel='{pref['tablet']}'",
        f"  * phone   → channel='{pref['phone']}'",
        "Read the active device class from CURRENTLY ON CANVAS and "
        "pick the matching channel. `channel` is required — never "
        "call `speak` without it.",
    ]


__all__ = ["render", "render_talk_preference"]
