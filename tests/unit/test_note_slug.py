"""Tests for the note slug helpers."""
from __future__ import annotations

from workshop.canvas.tools._slug import (
    is_valid_slug,
    slug_collides_with_existing,
    slug_from_markdown,
    slugify,
)


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


# ---- slug_collides_with_existing -------------------------------------------


def test_collision_flags_strict_token_superset() -> None:
    # Canonical case: candidate nests under an existing stored slug.
    assert slug_collides_with_existing(
        "steve-jobs-apple-comeback", ["steve-jobs"]
    ) == "steve-jobs"


def test_collision_flags_reordered_token_superset() -> None:
    # Token-set test, not prefix: order doesn't matter.
    assert slug_collides_with_existing(
        "apple-comeback-steve-jobs", ["steve-jobs"]
    ) == "steve-jobs"


def test_collision_returns_first_match() -> None:
    # Multiple existing slugs that both nest under candidate — first wins.
    result = slug_collides_with_existing(
        "steve-jobs-apple-comeback",
        ["steve-jobs", "apple-comeback"],
    )
    assert result in {"steve-jobs", "apple-comeback"}


def test_collision_ignores_exact_match() -> None:
    # Equal slugs are not a collision — that's an intentional overwrite.
    assert slug_collides_with_existing("steve-jobs", ["steve-jobs"]) is None


def test_collision_one_directional_subset_not_flagged() -> None:
    # Authoring a BROADER slug than something stored is allowed.
    # `jobs` (the role) vs stored `steve-jobs` — the polysemy false-positive
    # the inverse direction would produce. Must NOT flag.
    assert slug_collides_with_existing("jobs", ["steve-jobs"]) is None
    assert slug_collides_with_existing("apple", ["steve-jobs-apple"]) is None
    assert slug_collides_with_existing("python", ["monty-python"]) is None


def test_collision_distinct_tokens_not_flagged() -> None:
    # Neither slug is a subset of the other — different topics.
    assert slug_collides_with_existing(
        "roman-empire", ["byzantine-empire"]
    ) is None
    assert slug_collides_with_existing(
        "transformer-attention", ["transformer-tokenizer"]
    ) is None


def test_collision_empty_inputs() -> None:
    assert slug_collides_with_existing("", ["steve-jobs"]) is None
    assert slug_collides_with_existing("steve-jobs", []) is None
    assert slug_collides_with_existing("steve-jobs", [""]) is None
    assert slug_collides_with_existing(None, ["steve-jobs"]) is None  # type: ignore[arg-type]
