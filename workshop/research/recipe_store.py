"""Per-user JSON file storage for research recipes.

Layout:
  data/research/<user_id>/recipes/<recipe_id>.json

One file per recipe. Loading all of a user's recipes is cheap (≤100
files in the foreseeable future) so we do brute-force cosine in Python
rather than dragging in a vector DB. A small in-memory cache per user
avoids re-reading on rapid repeated lookups.

API is async to match the rest of the persona/teacher pipeline, even
though the underlying filesystem ops are synchronous — that lets us
swap in a network store later without changing callers.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID


# Repo root → data/research/<user>/recipes/. Resolved once at import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_ROOT = _REPO_ROOT / "data" / "research"


# ---- model -----------------------------------------------------------------


@dataclass
class ResearchRecipe:
    """One codified research procedure.

    Fields mirror the JSON on disk verbatim. `goal_embedding` is the
    cosine match key (768-dim nomic-embed-text vector). `tool_call_sequence`
    is the *parameterized* sequence — URLs and @ refs replaced with
    `{"$var": ...}` tokens; `workshop.research.recipe_parameterize.resolve`
    fills them at replay time. `recorded_refs` is the ARIA snapshot
    `(ref, role, name)` list from the original run — input to the smoke
    test that decides whether a replay is safe.
    """

    id: UUID
    host: str
    goal_text: str
    goal_embedding: List[float]
    tool_call_sequence: List[Dict[str, Any]]
    recorded_refs: List[Dict[str, Any]]
    success_count: int
    created_at: datetime
    last_used_at: datetime

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "host": self.host,
            "goal_text": self.goal_text,
            "goal_embedding": self.goal_embedding,
            "tool_call_sequence": self.tool_call_sequence,
            "recorded_refs": self.recorded_refs,
            "success_count": self.success_count,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "ResearchRecipe":
        return cls(
            id=UUID(data["id"]),
            host=data["host"],
            goal_text=data["goal_text"],
            goal_embedding=list(data.get("goal_embedding") or []),
            tool_call_sequence=list(data.get("tool_call_sequence") or []),
            recorded_refs=list(data.get("recorded_refs") or []),
            success_count=int(data.get("success_count", 1)),
            created_at=_parse_dt(data.get("created_at")),
            last_used_at=_parse_dt(data.get("last_used_at")),
        )


def _parse_dt(s: Optional[str]) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now(timezone.utc)


# ---- per-user cache --------------------------------------------------------
#
# Maps user_id → (cached_at_monotonic, {recipe_id: ResearchRecipe}).
# Invalidates on mutating calls (save / mark_used / delete) and after
# `_CACHE_TTL_S` seconds. Lookup loads all of a user's recipes once per
# Lane R turn; subsequent calls are zero-IO.

_CACHE_TTL_S = 60.0
_cache: Dict[str, "tuple[float, Dict[UUID, ResearchRecipe]]"] = {}


def _user_dir(user_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id) / "recipes"


def _ensure_user_dir(user_id: UUID) -> Path:
    p = _user_dir(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _invalidate_cache(user_id: UUID) -> None:
    _cache.pop(str(user_id), None)


def _load_all_uncached(user_id: UUID) -> Dict[UUID, ResearchRecipe]:
    p = _user_dir(user_id)
    if not p.is_dir():
        return {}
    out: Dict[UUID, ResearchRecipe] = {}
    for fp in p.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            recipe = ResearchRecipe.from_json(data)
            out[recipe.id] = recipe
        except Exception as e:
            # Corruption: skip but don't crash the lookup. The next
            # `record_after_success` will write a fresh recipe alongside.
            print(f"[recipe_store] skip corrupt {fp}: {e}", flush=True)
    return out


def _load_all(user_id: UUID) -> Dict[UUID, ResearchRecipe]:
    key = str(user_id)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_S:
        return cached[1]
    fresh = _load_all_uncached(user_id)
    _cache[key] = (now, fresh)
    return fresh


# ---- cosine ---------------------------------------------------------------


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity. Both vectors assumed non-empty and equal-length.
    Returns 0.0 on degenerate input rather than raising — a degenerate
    recipe just won't match anything."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---- public API ------------------------------------------------------------


async def save(user_id: UUID, recipe: ResearchRecipe) -> UUID:
    """Write `recipe` to `<user_dir>/<id>.json` atomically (tmp + replace).
    Returns the recipe id."""
    def _do() -> UUID:
        d = _ensure_user_dir(user_id)
        final = d / f"{recipe.id}.json"
        tmp = d / f".{recipe.id}.json.tmp"
        tmp.write_text(json.dumps(recipe.to_json(), indent=2), encoding="utf-8")
        os.replace(tmp, final)
        return recipe.id

    rid = await asyncio.to_thread(_do)
    _invalidate_cache(user_id)
    return rid


async def lookup(
    user_id: UUID,
    host: str,
    goal_embedding: List[float],
    threshold: float = 0.85,
) -> Optional[ResearchRecipe]:
    """Return the best recipe whose (host == host) AND cosine sim ≥ threshold,
    or None. Brute-force scan over the user's recipes."""
    all_recipes = await asyncio.to_thread(_load_all, user_id)
    if not all_recipes:
        return None
    best: Optional[ResearchRecipe] = None
    best_sim = -1.0
    for recipe in all_recipes.values():
        if recipe.host != host:
            continue
        sim = _cosine(goal_embedding, recipe.goal_embedding)
        if sim > best_sim:
            best_sim = sim
            best = recipe
    if best is None or best_sim < threshold:
        return None
    return best


async def mark_used(user_id: UUID, recipe_id: UUID) -> None:
    """Increment success_count and bump last_used_at. Idempotent if the
    recipe was deleted between calls."""
    def _do() -> None:
        path = _user_dir(user_id) / f"{recipe_id}.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        data["success_count"] = int(data.get("success_count", 0)) + 1
        data["last_used_at"] = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    await asyncio.to_thread(_do)
    _invalidate_cache(user_id)


async def list_for_user(user_id: UUID) -> List[ResearchRecipe]:
    """Return all recipes for a user (for debugging / future UI)."""
    all_recipes = await asyncio.to_thread(_load_all, user_id)
    return sorted(all_recipes.values(), key=lambda r: r.last_used_at, reverse=True)


async def delete(user_id: UUID, recipe_id: UUID) -> None:
    """Remove a recipe. Idempotent."""
    def _do() -> None:
        path = _user_dir(user_id) / f"{recipe_id}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    await asyncio.to_thread(_do)
    _invalidate_cache(user_id)


def make_recipe(
    *,
    host: str,
    goal_text: str,
    goal_embedding: List[float],
    tool_call_sequence: List[Dict[str, Any]],
    recorded_refs: List[Dict[str, Any]],
) -> ResearchRecipe:
    """Construct a fresh recipe with a new id + now() timestamps. Helper
    used by `recipes.record_after_success`."""
    now = datetime.now(timezone.utc)
    return ResearchRecipe(
        id=uuid.uuid4(),
        host=host,
        goal_text=goal_text,
        goal_embedding=goal_embedding,
        tool_call_sequence=tool_call_sequence,
        recorded_refs=recorded_refs,
        success_count=1,
        created_at=now,
        last_used_at=now,
    )


__all__ = [
    "ResearchRecipe",
    "save",
    "lookup",
    "mark_used",
    "list_for_user",
    "delete",
    "make_recipe",
]
