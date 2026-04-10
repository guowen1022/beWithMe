"""Exponential Moving Average (EMA) for preference embeddings.

After every interaction, the user's preference embedding is nudged toward the
interaction embedding. Over time this builds a dense fingerprint of what the
user reads and how they ask questions — capturing latent patterns that
categorical labels miss.
"""
from typing import List

EMA_ALPHA = 0.15  # ~7-interaction half-life: recent behavior matters more, old fades


def ema_update(
    current: List[float], new: List[float], alpha: float = EMA_ALPHA
) -> List[float]:
    """Blend a new interaction embedding into the running preference embedding."""
    return [alpha * n + (1.0 - alpha) * c for c, n in zip(current, new)]


def zero_embedding(dim: int = 768) -> List[float]:
    """Initial zero vector for a fresh user with no interactions."""
    return [0.0] * dim


def boost_query(
    query_embedding: List[float],
    preference_embedding: List[float],
    weight: float = 0.3,
) -> List[float]:
    """Blend a query embedding with the user's preference for retrieval.

    weight=0.3 means 30% preference, 70% query — the query still dominates,
    but results are nudged toward the user's overall interest profile.
    """
    return [
        (1.0 - weight) * q + weight * p
        for q, p in zip(query_embedding, preference_embedding)
    ]
