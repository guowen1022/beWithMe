"""Named LLM call profiles.

Call sites pick a profile by name (`profile="voice"`, `profile="engineer"`)
instead of threading model / thinking-mode / effort flags individually. A
profile is a small bundle of provider knobs:

  - `model`            — model name override; None falls back to the
                         provider's configured default (e.g.
                         settings.deepseek_model).
  - `thinking`         — whether DeepSeek's thinking mode is enabled.
                         DeepSeek's chat models default thinking-on; this
                         flag is mirrored to the provider's
                         `disable_thinking` knob.
  - `reasoning_effort` — DeepSeek's effort knob. Documented values:
                         "high" (default) and "max"; lower values
                         silently remap to "high".

Adding a new profile = one entry in `PROFILES`. Call sites don't change.
Profiles only carry provider-agnostic knobs; provider selection still
comes from `LLM_PROVIDER` in .env.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Profile:
    name: str
    model: Optional[str] = None
    thinking: bool = True
    reasoning_effort: Optional[str] = None


# Built-in profiles. Add entries here, not at call sites.
#
# - "default" — used when no profile is named. Backward-compatible:
#   no model override, thinking-on, no explicit effort (provider default).
# - "voice"   — Lane A voice-reply path. Same model as default
#   (v4-flash), thinking on with effort=high. Brevity is enforced
#   separately by the lane_a_voice skill prompt; the profile just
#   controls how much hidden reasoning the model is allowed to do.
# - "engineer" — frontend_engineer agent. Pro model (more capable),
#   thinking on with effort=max for the hardest reasoning.
PROFILES: dict[str, Profile] = {
    "default": Profile(name="default"),
    "voice": Profile(name="voice", reasoning_effort="high"),
    "engineer": Profile(
        name="engineer",
        model="deepseek-v4-pro",
        reasoning_effort="max",
    ),
}


def resolve(name: Optional[str]) -> Profile:
    """Look up a profile by name. Unknown names fall through to default
    rather than raising, so a typo in a call site doesn't kill the
    request — the run will just use defaults and the typo can be caught
    in code review."""
    if not name:
        return PROFILES["default"]
    return PROFILES.get(name, PROFILES["default"])


__all__ = ["Profile", "PROFILES", "resolve"]
