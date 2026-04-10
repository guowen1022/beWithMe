"""Half-Life Regression for concept mastery modeling."""

INITIAL_HALF_LIFE = 24.0  # hours
DECAY_HALF_LIFE_DAYS = 14  # for edge decay


def compute_mastery(half_life_hours: float, hours_since: float) -> float:
    """Recall probability: p = 2^(-dt/hl), clamped to [0, 1]."""
    if half_life_hours <= 0 or hours_since < 0:
        return 0.0
    return max(0.0, min(1.0, 2 ** (-hours_since / half_life_hours)))


def update_half_life(current_hl: float, recalled: bool, min_hl: float = 1.0, max_hl: float = 8760.0) -> float:
    """Success doubles half-life, failure halves it."""
    if recalled:
        return min(current_hl * 2.0, max_hl)
    return max(current_hl * 0.5, min_hl)


def mastery_to_state(p: float) -> str:
    if p >= 0.8:
        return "solid"
    if p >= 0.5:
        return "learning"
    if p >= 0.2:
        return "rusty"
    return "faded"
