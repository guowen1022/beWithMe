"""The teacher's own produced materials — durable, perception-independent.

The lead pass needs to know *what it has drawn* (so it can name "your LRU
diagram" and decide to route deep), and the deep pass needs the actual
*contents* of those notes to inspect/critique them. Both read from the durable
note store (`workshop.canvas.tools._note_cache`, written on every mount/edit and
persisted to `data/notes/<uid>/<slug>/source.md`) — NOT from the live-canvas
perception tracker, which is in-memory and gets wiped on any SSE reconnect.
That asymmetry is exactly what made the teacher say "I can't see the diagram I
just drew."

`collect_produced_notes` returns the most-recently-updated notes for a user,
each with its slug, a short title, how long ago it was updated, and its
markdown source. The lead inventory uses slug+title+age; the deep pass uses the
markdown.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from workshop.canvas.tools import _note_cache


def _title_from_md(md: Optional[str], slug: str) -> str:
    """First markdown heading, else the slug humanized."""
    if md:
        for line in md.splitlines():
            s = line.strip()
            if s.startswith("#"):
                title = s.lstrip("#").strip()
                if title:
                    return title
            if s:
                # First non-empty line isn't a heading — stop scanning so we
                # don't pull a body sentence as a title.
                break
    return slug.replace("-", " ").replace("_", " ").strip().title()


def _age_seconds(updated_at: str) -> Optional[float]:
    if not updated_at:
        return None
    try:
        dt = datetime.fromisoformat(updated_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def collect_produced_notes(
    user_id: UUID,
    limit: int = 5,
    max_age_s: Optional[float] = None,
) -> List[dict]:
    """Most-recently-updated notes the user has, newest first.

    Each item: {slug, title, age_s, updated_at, md}. `max_age_s`, when set,
    drops notes older than that (a coarse "this sitting" filter). Notes with no
    readable timestamp sort last. Best-effort — never raises into the caller.
    """
    try:
        slugs = _note_cache.list_slugs(user_id)
    except Exception:
        return []
    if not slugs:
        return []

    items: List[dict] = []
    for slug in slugs:
        try:
            meta = _note_cache.get_meta(user_id, slug)
            updated_at = (meta or {}).get("updated_at", "")
            age_s = _age_seconds(updated_at)
            if max_age_s is not None and age_s is not None and age_s > max_age_s:
                continue
            md = _note_cache.get_md(user_id, slug)
            if not md:
                continue
            items.append({
                "slug": slug,
                "title": _title_from_md(md, slug),
                "age_s": age_s,
                "updated_at": updated_at,
                "md": md,
            })
        except Exception:
            continue

    # Newest first; unknown age (None) sorts last.
    items.sort(key=lambda it: (it["age_s"] is None, it["age_s"] if it["age_s"] is not None else 0.0))
    return items[: max(0, limit)]


def _format_age(age_s: Optional[float]) -> str:
    if age_s is None:
        return "earlier"
    if age_s < 90:
        return f"{int(age_s)}s ago"
    if age_s < 5400:
        return f"{int(age_s // 60)}m ago"
    return f"{int(age_s // 3600)}h ago"


def render_inventory(notes: List[dict]) -> str:
    """Lightweight inventory for the lead prompt — titles only, no contents.

    Returns "" when there's nothing drawn, so the caller can skip the section.
    """
    if not notes:
        return ""
    lines = ["=== NOTES YOU'VE DRAWN (titles only — route deep to read their contents) ==="]
    for n in notes:
        lines.append(f"- \"{n['title']}\" (slug={n['slug']}, {_format_age(n['age_s'])})")
    return "\n".join(lines)


def render_full(notes: List[dict], max_chars: int = 4000) -> str:
    """Full markdown of produced notes for the deep prompt — the contents the
    deep pass inspects/critiques. Truncates each note to keep the prompt bounded.
    """
    if not notes:
        return ""
    chunks: List[str] = []
    for n in notes:
        md = (n.get("md") or "").strip()
        if len(md) > max_chars:
            md = md[:max_chars] + "\n…(truncated)"
        chunks.append(
            f"=== NOTE YOU DREW (slug={n['slug']}, {_format_age(n['age_s'])}) — MARKDOWN ===\n"
            f"{md}\n"
            "=== END ==="
        )
    return "\n\n".join(chunks)


__all__ = ["collect_produced_notes", "render_inventory", "render_full"]
