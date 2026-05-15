"""Slug helpers for note identifiers.

A note slug is a stable, human-readable identifier for one topic the teacher
has notes on (e.g. `sumer-mesopotamia`, `transformer-attention`). It doubles
as:

  * the canvas block_id (so the SSE topic stays scoped),
  * the on-disk filename (`data/notes/<user>/<slug>.md`),
  * the `note_id` column in `note_chunks` for vector search.

That triple use puts hard constraints on the shape:

  * lowercase ASCII letters, digits, and `-` only,
  * no leading/trailing `-`, no `--` runs,
  * not empty, at most 64 chars.

The LLM is expected to choose a topic-derived slug at mount time. If it
forgets, we fall back to slugifying the first markdown heading; failing
that, the caller falls back to the template's `id_default`.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional


_VALID_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$", flags=re.MULTILINE)
_MAX_LEN = 64


def is_valid_slug(s: str) -> bool:
    """True if `s` is already a well-formed slug (no normalization needed)."""
    return isinstance(s, str) and bool(_VALID_SLUG.match(s)) and len(s) <= _MAX_LEN


def slugify(s: str) -> str:
    """Normalize an arbitrary string to slug shape. Returns "" if nothing
    survives normalization (caller decides the fallback).
    """
    s = (s or "").lower().strip()
    # Drop everything that isn't a letter, digit, space, or hyphen.
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    # Whitespace runs → single hyphen.
    s = re.sub(r"\s+", "-", s)
    # Collapse hyphen runs and strip leading/trailing.
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:_MAX_LEN].rstrip("-")


def slug_from_markdown(md: str) -> Optional[str]:
    """Derive a slug from the first markdown heading. Returns None if no
    heading is found or the heading slugifies to empty.
    """
    if not md:
        return None
    m = _HEADING.search(md)
    if not m:
        return None
    slug = slugify(m.group(1))
    return slug or None


def slug_collides_with_existing(
    candidate: str,
    existing_slugs: Iterable[str],
) -> Optional[str]:
    """Detect when `candidate`'s tokens form a strict superset of an
    existing stored slug's tokens — almost always a sign the new slug
    is a facet/angle of an existing topic rather than a standalone
    entity.

    Token-set test: split each slug on `-`, compare as sets. Returns
    the first existing slug that nests under `candidate` (i.e. existing
    tokens ⊊ candidate tokens). Example: existing `steve-jobs` nests
    under candidate `steve-jobs-apple-comeback`.

    Deliberately one-directional: the inverse case (candidate is broader
    than an existing slug) isn't flagged. Authoring a broader concept is
    often legitimate, and polysemy makes that direction noisy (`python`
    the snake vs stored `monty-python`).

    Exact-equal slugs are NOT a collision — that's an intentional
    overwrite, handled elsewhere. Returns None when no collision.
    """
    if not isinstance(candidate, str) or not candidate:
        return None
    cand_tokens = set(candidate.split("-"))
    if not cand_tokens:
        return None
    for slug in existing_slugs:
        if not isinstance(slug, str) or not slug or slug == candidate:
            continue
        slug_tokens = set(slug.split("-"))
        if not slug_tokens:
            continue
        if slug_tokens < cand_tokens:
            return slug
    return None


__all__ = [
    "is_valid_slug",
    "slugify",
    "slug_from_markdown",
    "slug_collides_with_existing",
]
