"""End-to-end smoke for the recipe replay path.

Steps:
  1. Load the most recently recorded recipe from data/research/<user>/recipes/.
  2. Stub the persona-side IO (silicon_brain client, canvas push, enqueue,
     speak) the same way the benchmark does.
  3. Call `_execute_research_from_recipe` directly — verifies
     smoke_test + run_recipe + generate_cached synthesis end-to-end.
  4. Assert: speak was called with non-empty text, mark_used bumped
     success_count, total elapsed << fresh Lane R (~10-15 s vs 70-120 s).

Requires:
  - A recorded recipe in `data/research/<user>/recipes/` (run scenario 1
    first via the benchmark to record one).
  - The isolated browser sidecar from this worktree running at :18005
    (with the snapshot action).
  - `BASE_PORT=18000` so `tools.browser_set` resolves to :18005.
  - .env populated with LLM/embedding/vision creds.

Run:
    BASE_PORT=18000 .venv/bin/python tests/manual/test_recipe_replay.py
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _find_latest_recipe() -> tuple[uuid.UUID, dict]:
    candidates = sorted(
        glob.glob(str(_REPO / "data/research/*/recipes/*.json")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    if not candidates:
        raise SystemExit(
            "no recipes found under data/research/<user>/recipes/. Run a "
            "benchmark scenario first to record one."
        )
    fp = candidates[0]
    # Owning user_id is the directory two levels up.
    user_id = uuid.UUID(Path(fp).resolve().parents[1].name)
    data = json.loads(Path(fp).read_text())
    return user_id, data


async def _stub_canvas_push(*args, **kwargs):
    return None

async def _stub_enqueue(user_id, event):
    return 1

async def _stub_get_profile(self, user_id):
    return None

async def _stub_get_talk_preference(self, user_id):
    return {"desktop": "both", "tablet": "both", "phone": "text"}

async def _stub_aclose(self):
    return None

async def _stub_read_media(user_id):
    from infra.contracts.devices import MediaInventory
    return MediaInventory(canvases=[], voices=[])

async def _stub_get_user_profile(db, user_id, session_id=None):
    return None

async def _stub_get_concepts(db, user_id, limit=30):
    return []


async def main() -> int:
    user_id, recipe_data = _find_latest_recipe()
    print(f"[setup] user_id: {user_id}")
    print(f"[setup] recipe id: {recipe_data['id']}")
    print(f"[setup] host: {recipe_data['host']}")
    print(f"[setup] goal: {recipe_data['goal_text'][:80]}")
    print(f"[setup] success_count before: {recipe_data['success_count']}")
    print()

    # Reconstruct the recipe object the trigger expects.
    from workshop.research.recipe_store import ResearchRecipe
    recipe = ResearchRecipe.from_json(recipe_data)

    # Pick a URL with the same host — we'll replay the recipe against
    # the live page.
    runtime_url = "https://en.wikipedia.org/wiki/Photosynthesis"
    goal = "What is photosynthesis and what's the most surprising fact?"
    print(f"[setup] runtime URL: {runtime_url}")
    print(f"[setup] runtime goal: {goal}")
    print()

    # Stub patches.
    recorded_speaks = []
    async def _fake_speak(*, user_id, text, channel, **kw):
        recorded_speaks.append({"text": text, "channel": channel})
        return ["test-device"]

    from persona.teacher.silicon_brain_client import SiliconBrainClient
    from workshop.canvas.tools import read_media as _rm
    from services.persona.routers import dynamic as _dyn
    from persona.teacher import preferences as _prefs
    from persona.teacher import knowledge as _know
    from tools import speak as _speak_mod

    patches = [
        patch.object(SiliconBrainClient, "get_profile", _stub_get_profile),
        patch.object(SiliconBrainClient, "get_talk_preference", _stub_get_talk_preference),
        patch.object(SiliconBrainClient, "aclose", _stub_aclose),
        patch.object(_rm, "read_media", _stub_read_media),
        patch.object(_dyn, "enqueue_for_user", _stub_enqueue),
        patch.object(_prefs, "get_user_profile", _stub_get_user_profile),
        patch.object(_know, "get_concepts", _stub_get_concepts),
        patch.object(_speak_mod, "speak", _fake_speak),
    ]

    # Also patch _push_research_state_to_canvas in the manifest.
    from persona.teacher.tools import manifest as M
    patches.append(patch.object(M, "_push_research_state_to_canvas", _stub_canvas_push))

    for p in patches:
        p.start()
    try:
        from persona.teacher.triggers import _execute_research_from_recipe

        t0 = time.monotonic()
        await _execute_research_from_recipe(user_id, goal, runtime_url, recipe)
        elapsed = time.monotonic() - t0
    finally:
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass

    print()
    print("=" * 60)
    print(f"REPLAY ELAPSED: {elapsed:.1f}s")
    print(f"speak calls recorded: {len(recorded_speaks)}")
    if recorded_speaks:
        print(f"synthesis ({len(recorded_speaks[0]['text'])} chars):")
        print("  " + recorded_speaks[0]['text'][:600].replace("\n", " "))
    print()

    # Verdict
    fail = []
    if not recorded_speaks:
        fail.append("no speak call — synthesis did not deliver")
    elif len(recorded_speaks[0]["text"]) < 100:
        fail.append(f"synthesis too short: {len(recorded_speaks[0]['text'])} chars")
    if elapsed > 30:
        fail.append(f"replay too slow ({elapsed:.1f}s); should be < 30s")

    # Verify success_count bumped on disk.
    new_data = json.loads(Path(_REPO / f"data/research/{user_id}/recipes/{recipe.id}.json").read_text())
    if new_data["success_count"] <= recipe_data["success_count"]:
        fail.append(
            f"success_count not bumped: {new_data['success_count']} "
            f"<= {recipe_data['success_count']}"
        )
    else:
        print(
            f"success_count: {recipe_data['success_count']} -> "
            f"{new_data['success_count']} ✓"
        )

    if fail:
        print("\nVERDICT: FAIL")
        for f in fail:
            print(f"  - {f}")
        return 1
    print("\nVERDICT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
