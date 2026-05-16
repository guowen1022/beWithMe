"""Per-user persistent store + in-process cache for mounted notes.

The canvas writer needs to see the full content of an existing note to
decide between mounting fresh, appending to it, highlighting parts, or
revising specific text. Stored as markdown source (the writer's authoring
surface) and rendered HTML (what the client renders), with the HTML
keyed by viewport so a single note can have a wide and a narrow variant
rendered from the same source.

Disk layout (per slug):

    data/notes/<user_id>/<slug>/
      source.md             ← single source of truth
      meta.json             ← {created_at, updated_at}
      cache/
        wide.html           ← w >= h block layout
        narrow.html         ← w < h block layout

The slug itself is the directory name — never decorated with a viewport
suffix — because slugs are used for semantic recall and `list_slugs` /
`forget` must round-trip cleanly.

In-process cache mirrors the same shape: md is keyed by (user, slug),
html is keyed by (user, slug, viewport). Disable persistence in tests
with `NOTES_PERSIST=0`.

Legacy migration: notes written by an earlier version lived flat at
`data/notes/<uid>/<slug>.{md,html,meta.json}`. On first access for a
slug whose new-layout directory doesn't exist, we migrate the .md and
.meta.json into the new dir and delete the old single-variant .html
(which would have been rendered at the now-default "wide" viewport, and
will be regenerated on next mount anyway). Migration is idempotent.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_ROOT = _REPO_ROOT / "data" / "notes"

_DEFAULT_VIEWPORT = "wide"

# The public `set(user_id, block_id, ...)` cache mutator shadows the
# builtin inside this module. Keep a private alias so module-internal
# code can still build sets without a positional-args TypeError.
_set = set


def _persist_enabled() -> bool:
    return os.environ.get("NOTES_PERSIST", "1") != "0"


@dataclass
class CardEntry:
    md: Optional[str] = None
    # viewport name → rendered HTML
    html_by_viewport: dict[str, str] = field(default_factory=dict)


_cache: dict[Tuple[str, str], CardEntry] = {}


def _key(user_id: UUID, block_id: str) -> Tuple[str, str]:
    return (str(user_id), block_id)


def _user_dir(user_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id)


def _slug_dir(user_id: UUID, block_id: str) -> Path:
    return _user_dir(user_id) / block_id


def _md_path(user_id: UUID, block_id: str) -> Path:
    return _slug_dir(user_id, block_id) / "source.md"


def _meta_path(user_id: UUID, block_id: str) -> Path:
    return _slug_dir(user_id, block_id) / "meta.json"


def _cache_dir(user_id: UUID, block_id: str) -> Path:
    return _slug_dir(user_id, block_id) / "cache"


def _html_path(user_id: UUID, block_id: str, viewport: str) -> Path:
    return _cache_dir(user_id, block_id) / f"{viewport}.html"


# Legacy flat-layout paths (pre-viewport).
def _legacy_md_path(user_id: UUID, block_id: str) -> Path:
    return _user_dir(user_id) / f"{block_id}.md"


def _legacy_html_path(user_id: UUID, block_id: str) -> Path:
    return _user_dir(user_id) / f"{block_id}.html"


def _legacy_meta_path(user_id: UUID, block_id: str) -> Path:
    return _user_dir(user_id) / f"{block_id}.meta.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _migrate_legacy_if_needed(user_id: UUID, block_id: str) -> None:
    """Move legacy flat files into the new per-slug directory layout.

    Triggered lazily on read paths. Idempotent: a no-op once the slug dir
    exists or once no legacy files remain. Best-effort — never raises
    into the caller; if migration fails the legacy files just stay where
    they are and `_load_from_disk` returns None for that slug.
    """
    if not _persist_enabled():
        return
    slug_dir = _slug_dir(user_id, block_id)
    if slug_dir.exists():
        return
    legacy_md = _legacy_md_path(user_id, block_id)
    legacy_html = _legacy_html_path(user_id, block_id)
    legacy_meta = _legacy_meta_path(user_id, block_id)
    # Nothing to migrate — the slug just doesn't exist anywhere.
    if not (legacy_md.exists() or legacy_html.exists() or legacy_meta.exists()):
        return
    try:
        slug_dir.mkdir(parents=True, exist_ok=True)
        if legacy_md.exists():
            os.replace(legacy_md, _md_path(user_id, block_id))
        if legacy_meta.exists():
            os.replace(legacy_meta, _meta_path(user_id, block_id))
        # Drop the single-variant legacy HTML — it'll be regenerated at
        # the correct viewport on next mount.
        if legacy_html.exists():
            try:
                legacy_html.unlink()
            except OSError:
                pass
    except OSError:
        # Partial migration is fine; subsequent reads will retry.
        pass


def _touch_meta(user_id: UUID, block_id: str) -> None:
    """Stamp `created_at` on first write, refresh `updated_at` on every
    write. Best-effort — never raises into the caller."""
    if not _persist_enabled():
        return
    p = _meta_path(user_id, block_id)
    existing: dict = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    now = _now_iso()
    meta = {
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    try:
        _atomic_write(p, json.dumps(meta))
    except OSError:
        pass


def get_meta(user_id: UUID, block_id: str) -> dict:
    """Return `{created_at, updated_at}` ISO strings for this note.

    Source priority:
      1. Sidecar `<slug>/meta.json` (written by `set()`).
      2. Fallback: file mtime of `<slug>/source.md` (for legacy notes
         that predate the meta sidecar; both timestamps collapse to that
         single value).
      3. Empty dict if nothing is on disk.
    """
    if not _persist_enabled():
        return {}
    _migrate_legacy_if_needed(user_id, block_id)
    p = _meta_path(user_id, block_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "created_at": data.get("created_at") or "",
                    "updated_at": data.get("updated_at") or "",
                }
        except (OSError, json.JSONDecodeError):
            pass
    md_p = _md_path(user_id, block_id)
    if md_p.exists():
        try:
            ts = datetime.fromtimestamp(
                md_p.stat().st_mtime, tz=timezone.utc,
            ).isoformat()
            return {"created_at": ts, "updated_at": ts}
        except OSError:
            pass
    return {}


def _write_through(
    user_id: UUID,
    block_id: str,
    entry: CardEntry,
    *,
    wrote_md: bool,
    wrote_viewport: Optional[str],
    invalidated_viewports: Optional[list[str]] = None,
) -> None:
    if not _persist_enabled():
        return
    wrote_anything = False
    try:
        if wrote_md and entry.md is not None:
            _atomic_write(_md_path(user_id, block_id), entry.md)
            wrote_anything = True
        if wrote_viewport is not None:
            html = entry.html_by_viewport.get(wrote_viewport)
            if html is not None:
                _atomic_write(_html_path(user_id, block_id, wrote_viewport), html)
                wrote_anything = True
        for vp in invalidated_viewports or ():
            try:
                _html_path(user_id, block_id, vp).unlink()
            except FileNotFoundError:
                pass
    except OSError:
        # Best-effort persistence — never break the live mount because
        # disk is full / read-only. The in-process cache still works.
        pass
    if wrote_anything:
        _touch_meta(user_id, block_id)


def _load_from_disk(user_id: UUID, block_id: str) -> Optional[CardEntry]:
    if not _persist_enabled():
        return None
    _migrate_legacy_if_needed(user_id, block_id)
    md_p = _md_path(user_id, block_id)
    md = md_p.read_text(encoding="utf-8") if md_p.exists() else None
    html_by_viewport: dict[str, str] = {}
    cache_d = _cache_dir(user_id, block_id)
    if cache_d.exists():
        for p in cache_d.iterdir():
            if p.suffix == ".html" and p.is_file():
                try:
                    html_by_viewport[p.stem] = p.read_text(encoding="utf-8")
                except OSError:
                    continue
    if md is None and not html_by_viewport:
        return None
    return CardEntry(md=md, html_by_viewport=html_by_viewport)


def _get_or_hydrate(user_id: UUID, block_id: str) -> Optional[CardEntry]:
    k = _key(user_id, block_id)
    entry = _cache.get(k)
    if entry is not None:
        return entry
    disk = _load_from_disk(user_id, block_id)
    if disk is not None:
        _cache[k] = disk
    return disk


def set(  # noqa: A001 — keep the old name; callers grep `set`
    user_id: UUID,
    block_id: str,
    html: Optional[str] = None,
    md: Optional[str] = None,
    viewport: str = _DEFAULT_VIEWPORT,
) -> None:
    """Update the cache entry for (user_id, block_id) and write through
    to disk.

    Pass `md`, `html`, or both. Whichever is omitted keeps its prior
    value. `viewport` selects which html slot to write; md is
    viewport-agnostic.

    Cache coherence: when `md` is being written (the source of truth
    changed), every cached html variant *other than* `viewport` is
    invalidated — those variants were rendered from the old md and are
    now stale. They regenerate from fresh md on next mount via the
    `mount_template` hydration path. Setting only `html` (no `md`) does
    not invalidate other viewports.
    """
    if not block_id:
        return
    if html is None and md is None:
        return
    k = _key(user_id, block_id)
    entry = _get_or_hydrate(user_id, block_id) or CardEntry()
    wrote_md = False
    wrote_viewport: Optional[str] = None
    invalidated_viewports: list[str] = []
    if md is not None and isinstance(md, str):
        md_changed = entry.md != md
        entry.md = md
        wrote_md = True
        if md_changed:
            # Drop every stale viewport variant from memory; the on-disk
            # files for those viewports are deleted below in
            # `_write_through`. Skip the viewport we're about to write,
            # if any.
            # NB: `set` (the builtin) is shadowed by this function's
            # name, so we can't call set() here. Compare directly.
            keep_viewport = viewport if html is not None else None
            for vp in list(entry.html_by_viewport.keys()):
                if vp != keep_viewport:
                    invalidated_viewports.append(vp)
                    entry.html_by_viewport.pop(vp, None)
    if html is not None and isinstance(html, str):
        entry.html_by_viewport[viewport] = html
        wrote_viewport = viewport
    _cache[k] = entry
    _write_through(
        user_id,
        block_id,
        entry,
        wrote_md=wrote_md,
        wrote_viewport=wrote_viewport,
        invalidated_viewports=invalidated_viewports,
    )


def get_html(
    user_id: UUID,
    block_id: str,
    viewport: str = _DEFAULT_VIEWPORT,
) -> Optional[str]:
    entry = _get_or_hydrate(user_id, block_id)
    if entry is None:
        return None
    return entry.html_by_viewport.get(viewport)


def get_md(user_id: UUID, block_id: str) -> Optional[str]:
    entry = _get_or_hydrate(user_id, block_id)
    return entry.md if entry is not None else None


def get(
    user_id: UUID,
    block_id: str,
    viewport: str = _DEFAULT_VIEWPORT,
) -> Optional[str]:
    """Back-compat: return the HTML string for a viewport. New callers
    should use `get_html` / `get_md` explicitly."""
    return get_html(user_id, block_id, viewport=viewport)


def forget(user_id: UUID, block_id: str) -> None:
    _cache.pop(_key(user_id, block_id), None)
    if not _persist_enabled():
        return
    d = _slug_dir(user_id, block_id)
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError:
            pass
    # Also clean any straggler legacy files if migration never fired.
    for p in (
        _legacy_md_path(user_id, block_id),
        _legacy_html_path(user_id, block_id),
        _legacy_meta_path(user_id, block_id),
    ):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def forget_user(user_id: UUID) -> None:
    prefix = str(user_id)
    for k in [k for k in _cache if k[0] == prefix]:
        _cache.pop(k, None)
    if not _persist_enabled():
        return
    d = _user_dir(user_id)
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError:
            pass


def clear() -> None:
    """Drop the in-process cache only. Disk store is preserved — call
    `forget` / `forget_user` to delete from disk."""
    _cache.clear()


def list_slugs(user_id: UUID) -> list[str]:
    """Enumerate every stored note slug for a user (alphabetical).

    Source-of-truth is the on-disk layout under `data/notes/<uid>/`. We
    surface a slug if either:
      - The new-layout directory `<slug>/source.md` exists, or
      - A legacy flat file `<slug>.md` exists (will be migrated on
        first access).

    Returns [] when persistence is disabled or the user has no notes.
    """
    if not _persist_enabled():
        return []
    d = _user_dir(user_id)
    if not d.exists():
        return []
    slugs: set[str] = _set()
    for entry in d.iterdir():
        if entry.is_dir() and (entry / "source.md").exists():
            slugs.add(entry.name)
        elif entry.is_file() and entry.suffix == ".md":
            slugs.add(entry.stem)
    return sorted(slugs)
