"""Tests for the per-slug, viewport-aware note cache.

Disk layout under data/notes/<uid>/<slug>/ is:
    source.md
    meta.json
    cache/
        wide.html
        narrow.html

Each test uses a tmp_path-rooted data dir via monkeypatching _DATA_ROOT
so the production cache directory is never touched.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from workshop.canvas.tools import _note_cache


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the cache root to a fresh tmp dir per test and drop any
    process-wide in-memory state."""
    monkeypatch.setattr(_note_cache, "_DATA_ROOT", tmp_path / "notes")
    _note_cache.clear()
    yield
    _note_cache.clear()


UID = UUID("00000000-0000-0000-0000-000000000001")


def test_set_writes_per_slug_directory_layout(tmp_path: Path) -> None:
    _note_cache.set(UID, "quicksort", md="# Quicksort", html="<p>wide</p>", viewport="wide")

    slug_dir = _note_cache._DATA_ROOT / str(UID) / "quicksort"
    assert (slug_dir / "source.md").read_text() == "# Quicksort"
    assert (slug_dir / "meta.json").exists()
    assert (slug_dir / "cache" / "wide.html").read_text() == "<p>wide</p>"


def test_get_html_returns_per_viewport_content() -> None:
    _note_cache.set(UID, "qs", html="<p>wide</p>", viewport="wide")
    _note_cache.set(UID, "qs", html="<p>narrow</p>", viewport="narrow")

    assert _note_cache.get_html(UID, "qs", viewport="wide") == "<p>wide</p>"
    assert _note_cache.get_html(UID, "qs", viewport="narrow") == "<p>narrow</p>"
    # Unknown viewport → no entry.
    assert _note_cache.get_html(UID, "qs", viewport="bogus") is None


def test_get_html_default_viewport_is_wide() -> None:
    _note_cache.set(UID, "qs", html="<p>wide</p>", viewport="wide")
    assert _note_cache.get_html(UID, "qs") == "<p>wide</p>"


def test_md_update_invalidates_other_viewport_cache_entries() -> None:
    """When md is rewritten, every cached HTML variant other than the
    one being set in the same call is invalidated (in memory + on disk).
    """
    _note_cache.set(UID, "qs", md="v1", html="<p>wide-v1</p>", viewport="wide")
    _note_cache.set(UID, "qs", md="v1", html="<p>narrow-v1</p>", viewport="narrow")
    # Sanity: both viewports cached at this point.
    assert _note_cache.get_html(UID, "qs", viewport="wide") == "<p>wide-v1</p>"
    assert _note_cache.get_html(UID, "qs", viewport="narrow") == "<p>narrow-v1</p>"

    # Simulate edit_note: rewrite md and refresh wide only.
    _note_cache.set(UID, "qs", md="v2", html="<p>wide-v2</p>", viewport="wide")

    assert _note_cache.get_html(UID, "qs", viewport="wide") == "<p>wide-v2</p>"
    # Narrow variant was rendered from stale md; must be gone.
    assert _note_cache.get_html(UID, "qs", viewport="narrow") is None
    narrow_path = (
        _note_cache._DATA_ROOT / str(UID) / "qs" / "cache" / "narrow.html"
    )
    assert not narrow_path.exists()


def test_set_html_only_does_not_invalidate_other_viewports() -> None:
    """Pushing a fresh HTML render without changing md must leave the
    other viewport's cache alone (md is still authoritative for it)."""
    _note_cache.set(UID, "qs", md="v1", html="<p>wide-v1</p>", viewport="wide")
    _note_cache.set(UID, "qs", md="v1", html="<p>narrow-v1</p>", viewport="narrow")

    _note_cache.set(UID, "qs", html="<p>wide-v1b</p>", viewport="wide")

    assert _note_cache.get_html(UID, "qs", viewport="wide") == "<p>wide-v1b</p>"
    assert _note_cache.get_html(UID, "qs", viewport="narrow") == "<p>narrow-v1</p>"


def test_forget_rmtrees_the_slug_directory() -> None:
    _note_cache.set(UID, "qs", md="x", html="<p>wide</p>", viewport="wide")
    _note_cache.set(UID, "qs", html="<p>narrow</p>", viewport="narrow")
    slug_dir = _note_cache._DATA_ROOT / str(UID) / "qs"
    assert slug_dir.exists()

    _note_cache.forget(UID, "qs")

    assert not slug_dir.exists()
    assert _note_cache.get_html(UID, "qs", viewport="wide") is None
    assert _note_cache.get_html(UID, "qs", viewport="narrow") is None
    assert _note_cache.get_md(UID, "qs") is None


def test_list_slugs_finds_per_slug_dirs() -> None:
    _note_cache.set(UID, "alpha", md="a")
    _note_cache.set(UID, "beta", md="b")
    _note_cache.set(UID, "gamma", md="g")

    assert _note_cache.list_slugs(UID) == ["alpha", "beta", "gamma"]


def test_legacy_flat_layout_migrates_on_read(tmp_path: Path) -> None:
    """Notes written by the pre-viewport version sit flat at
    `<uid>/<slug>.md` and `<uid>/<slug>.meta.json`. On first read they
    should move into the per-slug directory layout; the legacy
    `<slug>.html` is dropped (will be regenerated at the right viewport
    on next mount)."""
    user_dir = _note_cache._DATA_ROOT / str(UID)
    user_dir.mkdir(parents=True, exist_ok=True)
    # Pre-seed legacy files.
    (user_dir / "qs.md").write_text("legacy source")
    (user_dir / "qs.meta.json").write_text('{"created_at":"t","updated_at":"t"}')
    (user_dir / "qs.html").write_text("legacy html")

    # First read triggers migration.
    md = _note_cache.get_md(UID, "qs")
    assert md == "legacy source"

    slug_dir = user_dir / "qs"
    assert (slug_dir / "source.md").read_text() == "legacy source"
    assert (slug_dir / "meta.json").exists()
    # Legacy flat files removed.
    assert not (user_dir / "qs.md").exists()
    assert not (user_dir / "qs.meta.json").exists()
    assert not (user_dir / "qs.html").exists()
    # HTML cache starts empty post-migration (gets regenerated on mount).
    assert _note_cache.get_html(UID, "qs", viewport="wide") is None


def test_legacy_slug_shows_in_list_slugs_before_migration() -> None:
    user_dir = _note_cache._DATA_ROOT / str(UID)
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "legacy-one.md").write_text("...")

    _note_cache.set(UID, "new-one", md="...")
    assert _note_cache.list_slugs(UID) == ["legacy-one", "new-one"]
