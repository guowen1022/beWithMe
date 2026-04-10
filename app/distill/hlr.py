"""Half-Life Regression (HLR) for concept mastery modeling.

Inspired by Duolingo's HLR: mastery decays exponentially over time.
Successful recall doubles the half-life; failure halves it.
"""

INITIAL_HALF_LIFE = 24.0  # hours


def compute_mastery(half_life_hours: float, hours_since_last_seen: float) -> float:
    """Compute recall probability: p = 2^(-dt/hl), clamped to [0, 1]."""
    if half_life_hours <= 0 or hours_since_last_seen < 0:
        return 0.0
    p = 2 ** (-hours_since_last_seen / half_life_hours)
    return max(0.0, min(1.0, p))


def update_half_life(
    current_hl: float,
    recalled: bool,
    min_hl: float = 1.0,
    max_hl: float = 8760.0,
) -> float:
    """Adjust half-life after an encounter.

    Success doubles it (stronger memory), failure halves it (weaker memory).
    """
    if recalled:
        return min(current_hl * 2.0, max_hl)
    return max(current_hl * 0.5, min_hl)


def mastery_to_state(p: float) -> str:
    """Map mastery probability to a human-readable state label."""
    if p >= 0.8:
        return "solid"
    if p >= 0.5:
        return "learning"
    if p >= 0.2:
        return "rusty"
    return "faded"
