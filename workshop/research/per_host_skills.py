"""Per-host navigation notes — shared (global tier) cheatsheets by URL host.

After a successful Lane R run, the persona reflects on what it learned
about the *site* (not the topic) and saves a short prose note keyed by
host. The next time research starts on that host (any user, any topic),
the saved note is prepended to the prompt so the agent doesn't
re-discover the same navigation tricks.

Different from recipes (`workshop/research/recipe_store`):
  * Recipes are deterministic tool sequences keyed by (host, goal
    embedding). They REPLACE the LLM-driven research loop for matching
    intents. Per-user.
  * Per-host skills are PROSE keyed by host alone. They AUGMENT the
    LLM's prompt; the loop still runs. **Global** — all users on this
    installation share the library, because the navigation facts they
    capture are about public sites, not about any one user.

**Naming:** "per-host" not "domain". The word "domain" is reserved for
the future broader concept of a *knowledge domain* (finance, medicine,
history) — a different axis. A per-host skill is one specific kind of
knowledge-domain skill; the terms must not collide.

Storage: `data/per-host-skills/<host>.md`, one file per host.
Markdown with YAML-ish frontmatter:

    ---
    host: en.wikipedia.org
    use_count: 3
    created_at: 2026-05-12T10:30:00Z
    updated_at: 2026-05-12T14:15:00Z
    ---
    Wikipedia articles open with an infobox containing the key facts
    (dates, locations, dynasties) in a sidebar — `read_url`'s text
    extraction skips this. Use `browser_set(action='snapshot')` then
    `text @e<n>` on the heading whose name starts with the article
    title.

`save()` does an LLM-mediated merge when a prior note exists, so the
file stays coherent rather than accumulating a chronological log.
Falls back to plain append (FIFO trim) if the merge LLM is unavailable.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "per-host-skills"
_MAX_NOTE_CHARS = 4000


# Frontmatter parser. Hand-rolled (no PyYAML dependency) — three scalar
# fields between two `---` fences.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


@dataclass
class PerHostSkill:
    host: str
    note: str
    use_count: int = 0
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"host: {self.host}",
            f"use_count: {self.use_count}",
            f"created_at: {self.created_at.isoformat()}",
            f"updated_at: {self.updated_at.isoformat()}",
            "---",
            "",
            self.note.strip(),
            "",
        ]
        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, text: str, fallback_host: str) -> "PerHostSkill":
        m = _FRONTMATTER_RE.match(text)
        if not m:
            # File missing frontmatter — treat whole body as note.
            return cls(host=fallback_host, note=text.strip())
        meta_str = m.group("meta")
        body = m.group("body").strip()

        meta: dict = {}
        for line in meta_str.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

        host = meta.get("host") or fallback_host
        try:
            use_count = int(meta.get("use_count", "0"))
        except ValueError:
            use_count = 0

        def _parse_dt(s: str) -> datetime:
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return datetime.now(timezone.utc)

        return cls(
            host=host,
            note=body,
            use_count=use_count,
            created_at=_parse_dt(meta.get("created_at", "")),
            updated_at=_parse_dt(meta.get("updated_at", "")),
        )


# ---- helpers ------------------------------------------------------------

_HOST_OK = re.compile(r"^[a-z0-9][a-z0-9.\-_]*$", re.IGNORECASE)

# One lock for all saves — the file set is small and write rate is low
# (one save per finished Lane R turn, which is human-scale). Serializing
# writes is simpler and safe than per-host locks here.
_lock = asyncio.Lock()


def _safe_host(host: str) -> str:
    """Reject hostnames that would escape the directory. Only letters,
    digits, dots, dashes, underscores — and never `..` substrings."""
    host = (host or "").strip().lower()
    if not host or not _HOST_OK.match(host) or ".." in host:
        raise ValueError(f"unsafe host {host!r}")
    return host


def _ensure_root() -> Path:
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT


def _path_for(host: str) -> Path:
    return _ensure_root() / f"{_safe_host(host)}.md"


def _atomic_write(p: Path, text: str) -> None:
    """Tmp file + rename. Never leaves a half-written .md on disk."""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


# ---- LLM-mediated merge ------------------------------------------------

# Tests stub this; production wires it to infra.model.llm.generate_cached.
async def _llm_merge(host: str, existing_note: str, new_paragraph: str) -> str:
    """Ask the LLM to merge `new_paragraph` into `existing_note` for
    `host`. Falls back to plain append on any failure."""
    from infra.model.llm import generate_cached

    system = (
        f"You're maintaining a navigation cheatsheet for the website `{host}`. "
        "Below is the current cheatsheet, then a new observation. Produce an "
        "updated cheatsheet that:\n"
        "- Keeps all still-true existing entries\n"
        "- Adds the new observation if it's not already covered\n"
        "- Drops any entry the new observation contradicts (newer wins)\n"
        "- Removes redundancy\n"
        "- Stays under 4000 characters\n"
        "- Reads as a coherent set of navigation tips, not a chronological log\n"
        "\n"
        "If the new observation is already fully covered by the existing "
        "cheatsheet, return the existing cheatsheet verbatim. Output ONLY "
        "the merged cheatsheet text — no preamble, no commentary."
    )
    dynamic_user = (
        "=== CURRENT CHEATSHEET ===\n"
        f"{existing_note.strip()}\n\n"
        "=== NEW OBSERVATION ===\n"
        f"{new_paragraph.strip()}\n"
    )
    try:
        text, _usage = await generate_cached(
            static_system=system,
            static_user_passage="",
            dynamic_user=dynamic_user,
            prior_messages=None,
            max_tokens=1024,
            purpose="per-host-skill-merge",
        )
    except Exception as e:
        print(f"[per_host_skills] merge LLM failed: {e}; falling back to append", flush=True)
        return _append_with_trim(existing_note, new_paragraph)

    cleaned = (text or "").strip()
    if not cleaned:
        return _append_with_trim(existing_note, new_paragraph)
    # Safety: enforce the size cap even if the LLM exceeded it.
    if len(cleaned) > _MAX_NOTE_CHARS:
        cleaned = cleaned[:_MAX_NOTE_CHARS].rsplit("\n\n", 1)[0]
    return cleaned


def _append_with_trim(existing_note: str, new_paragraph: str) -> str:
    """Fallback merge: plain append, then FIFO trim to fit cap."""
    combined = (existing_note.rstrip() + "\n\n" + new_paragraph.strip()).strip()
    if len(combined) <= _MAX_NOTE_CHARS:
        return combined
    # Drop oldest paragraphs until under cap; snap to paragraph boundary.
    sliced = combined[-_MAX_NOTE_CHARS:]
    idx = sliced.find("\n\n")
    return sliced[idx + 2:] if idx >= 0 else sliced


# ---- public API ---------------------------------------------------------

async def get(host: str) -> Optional[PerHostSkill]:
    """Return the saved skill for `host`, or None if missing/unsafe."""
    try:
        safe = _safe_host(host)
    except ValueError:
        return None
    p = _path_for(safe)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    return PerHostSkill.from_markdown(text, fallback_host=safe)


async def save(
    host: str,
    new_paragraph: str,
) -> Optional[PerHostSkill]:
    """Add a navigation observation for `host`. Creates the file on
    first save; LLM-merges into the existing note on subsequent saves.

    Empty / `NO_NOTE` / `NONE` paragraphs are ignored — the reflection
    LLM may decide no patterns are worth saving, and we don't want to
    spam the file with empty entries.
    """
    if not new_paragraph or not new_paragraph.strip():
        return None
    paragraph = new_paragraph.strip()
    if paragraph.upper() in ("NO_NOTE", "(NO_NOTE)", "NONE", "N/A"):
        return None

    try:
        safe = _safe_host(host)
    except ValueError:
        return None

    now = datetime.now(timezone.utc)
    async with _lock:
        existing = await get(safe)
        if existing is None:
            # Trim a fresh first paragraph to fit the cap if it's gigantic.
            note = paragraph[:_MAX_NOTE_CHARS]
            skill = PerHostSkill(
                host=safe,
                note=note,
                use_count=0,
                created_at=now,
                updated_at=now,
            )
        else:
            merged = await _llm_merge(safe, existing.note, paragraph)
            # If LLM returned essentially the existing note verbatim, skip
            # writing — saves IO and avoids touching updated_at.
            if merged.strip() == existing.note.strip():
                print(
                    f"[per_host_skills] merge produced no-op for {safe} "
                    f"(use_count={existing.use_count})",
                    flush=True,
                )
                return existing
            skill = PerHostSkill(
                host=safe,
                note=merged,
                use_count=existing.use_count,
                created_at=existing.created_at,
                updated_at=now,
            )
        _atomic_write(_path_for(safe), skill.to_markdown())
        print(
            f"[per_host_skills] saved {safe} "
            f"(note={len(skill.note)} chars, use_count={skill.use_count})",
            flush=True,
        )
        return skill


async def mark_used(host: str) -> None:
    """Bump use_count + updated_at. Called when a note is injected into
    a research prompt. Cheap; non-blocking failure tolerated."""
    try:
        safe = _safe_host(host)
    except ValueError:
        return
    async with _lock:
        existing = await get(safe)
        if existing is None:
            return
        existing.use_count += 1
        existing.updated_at = datetime.now(timezone.utc)
        try:
            _atomic_write(_path_for(safe), existing.to_markdown())
        except OSError as e:
            print(f"[per_host_skills] mark_used write failed for {safe}: {e}", flush=True)


async def list_all() -> List[PerHostSkill]:
    root = _ensure_root()
    skills: List[PerHostSkill] = []
    for path in sorted(root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            skills.append(PerHostSkill.from_markdown(text, fallback_host=path.stem))
        except Exception as e:
            print(f"[per_host_skills] skip corrupt {path}: {e}", flush=True)
    return skills


async def delete(host: str) -> None:
    try:
        safe = _safe_host(host)
    except ValueError:
        return
    p = _path_for(safe)
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            print(f"[per_host_skills] delete failed for {safe}: {e}", flush=True)


__all__ = [
    "PerHostSkill",
    "get",
    "save",
    "mark_used",
    "list_all",
    "delete",
]
