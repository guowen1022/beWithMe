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
from typing import Optional


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


__all__ = ["is_valid_slug", "slugify", "slug_from_markdown"]
