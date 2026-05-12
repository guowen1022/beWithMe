"""Unit smoke for workshop/research — file store + parameterize round-trip.

No browser sidecar, no LLM, no network. Verifies:
  - save/list/lookup/mark_used/delete on a tmp data dir
  - cosine match prefers exact-goal recipe over distant-goal recipe
  - mismatched host returns None
  - parameterize → resolve round-trip restores URLs + remapped refs
  - corrupted JSON file is skipped (not crash)

Run:
    .venv/bin/python tests/manual/test_recipe_store.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

# Repo root on sys.path so `workshop.research` resolves.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Stable env so infra.config can import.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("LLM_PROVIDER", "deepseek")
os.environ.setdefault("DEEPSEEK_API_KEY", "x")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://x")
os.environ.setdefault("DEEPSEEK_MODEL", "x")
os.environ.setdefault("VISION_PROVIDER", "doubao")
os.environ.setdefault("DOUBAO_API_KEY", "x")
os.environ.setdefault("DOUBAO_BASE_URL", "https://x")
os.environ.setdefault("DOUBAO_VISION_MODEL", "x")


from workshop.research import recipe_parameterize, recipe_store  # noqa: E402


async def _redirect_data_root(tmp: Path):
    """Point the store at a tmp dir for this test."""
    recipe_store._DATA_ROOT = tmp
    # Invalidate any per-user cache so prior tests don't poison this one.
    recipe_store._cache.clear()


def _fake_embedding(seed: int, dim: int = 768) -> list[float]:
    """Tiny deterministic vector. Seed 1 ≈ seed 1; seed 1 vs seed 99 distant."""
    # Simple sin-based generator gives non-degenerate, distinct vectors.
    import math
    return [math.sin(seed * 0.31 + i * 0.07) for i in range(dim)]


async def main() -> int:
    tmp = Path("/tmp/bewithme-recipe-test")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    await _redirect_data_root(tmp)

    uid = uuid.uuid4()

    # 1. save two recipes for same host, different intents
    r_close_a = recipe_store.make_recipe(
        host="en.wikipedia.org",
        goal_text="opinion of this stock",
        goal_embedding=_fake_embedding(1),
        tool_call_sequence=[
            {"name": "browser_set", "arguments": {"action": "goto", "url": "https://en.wikipedia.org/foo"}},
            {"name": "browser_set", "arguments": {"action": "snapshot"}},
            {"name": "browser_set", "arguments": {"action": "text", "selector": "@e42"}},
        ],
        recorded_refs=[{"ref": "@e42", "role": "heading", "name": "Criticisms"}],
    )
    r_close_b = recipe_store.make_recipe(
        host="en.wikipedia.org",
        goal_text="quick read on bitcoin",
        goal_embedding=_fake_embedding(99),  # very different
        tool_call_sequence=[],
        recorded_refs=[],
    )
    await recipe_store.save(uid, r_close_a)
    await recipe_store.save(uid, r_close_b)

    listed = await recipe_store.list_for_user(uid)
    assert len(listed) == 2, f"expected 2 recipes, got {len(listed)}"
    print(f"[1] save+list OK ({len(listed)} recipes)")

    # 2. lookup: query with embedding close to r_close_a's
    match = await recipe_store.lookup(
        uid, host="en.wikipedia.org",
        goal_embedding=_fake_embedding(1),
        threshold=0.85,
    )
    assert match is not None, "lookup found no match"
    assert match.id == r_close_a.id, (
        f"lookup returned wrong recipe: {match.id} != {r_close_a.id}"
    )
    print(f"[2] lookup picked the close recipe OK ({match.goal_text!r})")

    # 3. wrong host → no match
    miss = await recipe_store.lookup(
        uid, host="other.com",
        goal_embedding=_fake_embedding(1),
        threshold=0.85,
    )
    assert miss is None, f"host filter broken: {miss}"
    print("[3] host filter OK")

    # 4. mark_used bumps counter
    before = (await recipe_store.list_for_user(uid))[0]
    assert before.success_count == 1
    await recipe_store.mark_used(uid, r_close_a.id)
    after = next(r for r in await recipe_store.list_for_user(uid) if r.id == r_close_a.id)
    assert after.success_count == 2, f"mark_used didn't bump: {after.success_count}"
    assert after.last_used_at >= before.last_used_at
    print(f"[4] mark_used OK ({after.success_count=})")

    # 5. parameterize → resolve round-trip
    sequence = [
        {"name": "browser_set", "arguments": {
            "action": "goto",
            "url": "https://example.com/page",
        }},
        {"name": "browser_set", "arguments": {"action": "snapshot"}},
        {"name": "browser_set", "arguments": {
            "action": "text",
            "selector": "@e42",
        }},
    ]
    refs = [
        {"ref": "@e42", "role": "heading", "name": "Findings"},
    ]
    parameterized = recipe_parameterize.parameterize(sequence, refs)
    # URL must be tokenized
    assert parameterized[0]["arguments"]["url"] == {"$var": "page_url"}, parameterized[0]
    # snapshot is a constant — no args
    assert parameterized[1]["arguments"] == {"action": "snapshot"}
    # @e42 is tokenized with role/name
    sel = parameterized[2]["arguments"]["selector"]
    assert sel == {"$var": "ref", "role": "heading", "name": "Findings"}, sel
    print("[5] parameterize OK")

    runtime = {
        "page_url": "https://other.example.com/different",
        "secondary_urls": [],
        "ref_remap": {("heading", "Findings"): "@e7"},
    }
    resolved = recipe_parameterize.resolve(parameterized, runtime)
    assert resolved[0]["arguments"]["url"] == "https://other.example.com/different"
    assert resolved[2]["arguments"]["selector"] == "@e7"
    print("[6] resolve OK")

    # 7. corrupted file is skipped, not crash
    corrupt = recipe_store._user_dir(uid) / "garbage.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")
    # Cache hasn't expired, force-invalidate
    recipe_store._invalidate_cache(uid)
    listed = await recipe_store.list_for_user(uid)
    assert len(listed) == 2, f"corrupted file caused load failure: got {len(listed)}"
    print("[7] corruption-tolerance OK")

    # 8. delete
    await recipe_store.delete(uid, r_close_b.id)
    listed = await recipe_store.list_for_user(uid)
    assert len(listed) == 1
    assert listed[0].id == r_close_a.id
    print("[8] delete OK")

    print()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
