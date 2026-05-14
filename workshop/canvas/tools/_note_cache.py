"""Per-user persistent store + in-process cache for mounted notes.

The canvas writer needs to see the full content of an existing note to
decide between mounting fresh, appending to it, highlighting parts, or
revising specific text. Phase 2.5: stored as both markdown source (the
writer's authoring surface) and rendered HTML (what the client renders).

  (user_id, block_id) → {"md": <source markdown>, "html": <final HTML>}

`md` is the source of truth; `html` is a derived rendering. `edit_note`
ops operate on `md`, then re-render to `html`. Legacy HTML-only mounts
populate `html` with no `md`; readers fall back gracefully.

Updated whenever we know either side changed:
  * `mount_template` (note path) — after sanitize + diagram inline
  * `push_block_content` (note content topic) — after preprocessing
  * `edit_note` — after applying ops and re-rendering markdown

Persistence: write-through to `data/notes/<user_id>/<block_id>.{md,html}`
so notes survive process restarts. On cache miss, lazily read from disk
and re-hydrate. Same per-user-file shape as `data/research/` and
`data/per-host-skills/`. Disable persistence in tests with the
environment variable `NOTES_PERSIST=0`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_ROOT = _REPO_ROOT / "data" / "notes"


def _persist_enabled() -> bool:
    return os.environ.get("NOTES_PERSIST", "1") != "0"


@dataclass
class CardEntry:
    md: Optional[str] = None
    html: Optional[str] = None


_cache: dict[Tuple[str, str], CardEntry] = {}


def _key(user_id: UUID, block_id: str) -> Tuple[str, str]:
    return (str(user_id), block_id)


def _dir_for(user_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id)


def _md_path(user_id: UUID, block_id: str) -> Path:
    return _dir_for(user_id) / f"{block_id}.md"


def _html_path(user_id: UUID, block_id: str) -> Path:
    return _dir_for(user_id) / f"{block_id}.html"


def _meta_path(user_id: UUID, block_id: str) -> Path:
    return _dir_for(user_id) / f"{block_id}.meta.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


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
      1. Sidecar `<slug>.meta.json` (written by `set()`).
      2. Fallback: file mtime of `<slug>.md` (for legacy notes that
         predate the meta sidecar; both timestamps collapse to that
         single value).
      3. Empty dict if nothing is on disk.
    """
    if not _persist_enabled():
        return {}
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


def _write_through(user_id: UUID, block_id: str, entry: CardEntry) -> None:
    if not _persist_enabled():
        return
    wrote_anything = False
    try:
        if entry.md is not None:
            _atomic_write(_md_path(user_id, block_id), entry.md)
            wrote_anything = True
        if entry.html is not None:
            _atomic_write(_html_path(user_id, block_id), entry.html)
            wrote_anything = True
    except OSError:
        # Best-effort persistence — never break the live mount because
        # disk is full / read-only. The in-process cache still works.
        pass
    if wrote_anything:
        _touch_meta(user_id, block_id)


def _load_from_disk(user_id: UUID, block_id: str) -> Optional[CardEntry]:
    if not _persist_enabled():
        return None
    md_p = _md_path(user_id, block_id)
    html_p = _html_path(user_id, block_id)
    md = md_p.read_text(encoding="utf-8") if md_p.exists() else None
    html = html_p.read_text(encoding="utf-8") if html_p.exists() else None
    if md is None and html is None:
        return None
    return CardEntry(md=md, html=html)


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
) -> None:
    """Update the cache entry for (user_id, block_id) and write through
    to disk.

    Pass `md`, `html`, or both. Whichever is omitted keeps its prior
    value (so a markdown-only edit doesn't blow away a previously
    rendered HTML, and vice versa)."""
    if not block_id:
        return
    if html is None and md is None:
        return
    k = _key(user_id, block_id)
    entry = _get_or_hydrate(user_id, block_id) or CardEntry()
    if html is not None and isinstance(html, str):
        entry.html = html
    if md is not None and isinstance(md, str):
        entry.md = md
    _cache[k] = entry
    _write_through(user_id, block_id, entry)


def get_html(user_id: UUID, block_id: str) -> Optional[str]:
    entry = _get_or_hydrate(user_id, block_id)
    return entry.html if entry is not None else None


def get_md(user_id: UUID, block_id: str) -> Optional[str]:
    entry = _get_or_hydrate(user_id, block_id)
    return entry.md if entry is not None else None


def get(user_id: UUID, block_id: str) -> Optional[str]:
    """Back-compat: return the HTML string. New callers should use
    `get_html` / `get_md` explicitly."""
    return get_html(user_id, block_id)


def forget(user_id: UUID, block_id: str) -> None:
    _cache.pop(_key(user_id, block_id), None)
    if _persist_enabled():
        for p in (
            _md_path(user_id, block_id),
            _html_path(user_id, block_id),
            _meta_path(user_id, block_id),
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
    if _persist_enabled():
        d = _dir_for(user_id)
        if d.exists():
            for p in d.iterdir():
                try:
                    p.unlink()
                except OSError:
                    pass


def clear() -> None:
    """Drop the in-process cache only. Disk store is preserved — call
    `forget` / `forget_user` to delete from disk."""
    _cache.clear()
