"""Tests for the note slug helpers."""
from __future__ import annotations

from workshop.canvas.tools._slug import is_valid_slug, slug_from_markdown, slugify


def test_is_valid_slug_accepts_kebab() -> None:
    assert is_valid_slug("sumer")
    assert is_valid_slug("sumer-mesopotamia")
    assert is_valid_slug("transformer-attention-v2")
    assert is_valid_slug("a")


def test_is_valid_slug_rejects_bad_shapes() -> None:
    assert not is_valid_slug("")
    assert not is_valid_slug("Sumer")            # uppercase
    assert not is_valid_slug("sumer-")           # trailing hyphen
    assert not is_valid_slug("-sumer")           # leading hyphen
    assert not is_valid_slug("sumer--meso")      # double hyphen
    assert not is_valid_slug("sumer_meso")       # underscore
    assert not is_valid_slug("sumer mesopotamia")  # space
    assert not is_valid_slug("sumer/mesopotamia")  # slash
    assert not is_valid_slug("../etc/passwd")    # traversal
    assert not is_valid_slug("a" * 65)            # too long


def test_slugify_normalizes() -> None:
    assert slugify("Sumer in Mesopotamia") == "sumer-in-mesopotamia"
    assert slugify("  Transformer  Attention!  ") == "transformer-attention"
    assert slugify("Self-Attention  &  Heads") == "self-attention-heads"
    assert slugify("") == ""
    assert slugify("---") == ""
    # Truncates at 64 chars without leaving a trailing hyphen.
    s = slugify("a-" * 50)  # 100 chars
    assert len(s) <= 64
    assert not s.endswith("-")


def test_slug_from_markdown_uses_first_heading() -> None:
    md = "## Sumer in Mesopotamia\n\nThe earliest civilization…"
    assert slug_from_markdown(md) == "sumer-in-mesopotamia"

    md = "# The Earliest Civilization\n\n## Sub heading"
    assert slug_from_markdown(md) == "the-earliest-civilization"


def test_slug_from_markdown_returns_none_when_no_heading() -> None:
    assert slug_from_markdown("plain text, no heading") is None
    assert slug_from_markdown("") is None
    assert slug_from_markdown(None) is None  # type: ignore[arg-type]


def test_slug_from_markdown_returns_none_for_empty_heading() -> None:
    assert slug_from_markdown("## !!!\n\nbody") is None  # nothing survives slugify
