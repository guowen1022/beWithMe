"""Real generalization test for recipe replay.

The Phase 2 replay smoke ran the SAME goal twice — a weak test. This
script asks two *different* questions of the same shape on the same
host, to verify recipes actually generalize semantically:

  Phase A (RECORD): "What's the impact on Europe of Genghis Khan?"
                    URL: https://en.wikipedia.org/wiki/Genghis_Khan
                    → drives full Lane R; records a recipe.

  Phase B (REPLAY): "What's the impact on Europe of King Arthur?"
                    URL: https://en.wikipedia.org/wiki/King_Arthur
                    → embed goal; lookup; if hit, replay against the
                      King Arthur page; otherwise fall through.

Verdict:
  - Records phase A produces a recipe on disk.
  - Phase B's cosine similarity between (Genghis goal embedding,
    King Arthur goal embedding) is reported. ≥ 0.85 = hit.
  - If hit: total elapsed for phase B is reported (~5-15s expected).
  - Synthesis text from both phases is printed for human spot-check.

Requires:
  - Isolated worktree browser sidecar on :18005.
  - BASE_PORT=18000 env var so `tools.browser_set` resolves to :18005.
  - .env populated with DeepSeek + Doubao + Ollama creds.

Run:
    BASE_PORT=18000 .venv/bin/python tests/manual/test_recipe_generalization.py
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ---- scenario ----------------------------------------------------------

GENGHIS_GOAL = "What's the impact on Europe of Genghis Khan?"
GENGHIS_URL = "https://en.wikipedia.org/wiki/Genghis_Khan"

ARTHUR_GOAL = "What's the impact on Europe of King Arthur?"
ARTHUR_URL = "https://en.wikipedia.org/wiki/King_Arthur"


# ---- stubs (copied/aligned with the existing replay smoke) ------------

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


def _patches(fake_speak):
    from persona.teacher.silicon_brain_client import SiliconBrainClient
    from workshop.canvas.tools import read_media as _rm
    from services.persona.routers import dynamic as _dyn
    from persona.teacher import preferences as _prefs
    from persona.teacher import knowledge as _know
    from tools import speak as _speak_mod
    from persona.teacher.tools import manifest as M

    return [
        patch.object(SiliconBrainClient, "get_profile", _stub_get_profile),
        patch.object(SiliconBrainClient, "get_talk_preference", _stub_get_talk_preference),
        patch.object(SiliconBrainClient, "aclose", _stub_aclose),
        patch.object(_rm, "read_media", _stub_read_media),
        patch.object(_dyn, "enqueue_for_user", _stub_enqueue),
        patch.object(_prefs, "get_user_profile", _stub_get_user_profile),
        patch.object(_know, "get_concepts", _stub_get_concepts),
        patch.object(_speak_mod, "speak", fake_speak),
        patch.object(M, "_push_research_state_to_canvas", _stub_canvas_push),
    ]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> int:
    # Always start with an empty recipe store so we measure a true
    # cold→warm transition.
    user_id = uuid.uuid4()
    user_dir = _REPO / "data" / "research" / str(user_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)

    speaks_a: list[dict] = []
    speaks_b: list[dict] = []

    async def fake_speak_a(*, user_id, text, channel, **kw):
        speaks_a.append({"text": text, "channel": channel})
        return ["test"]
    async def fake_speak_b(*, user_id, text, channel, **kw):
        speaks_b.append({"text": text, "channel": channel})
        return ["test"]

    # ===== PHASE A: RECORD =====
    print("=" * 70)
    print(f"PHASE A — RECORD")
    print(f"  goal: {GENGHIS_GOAL}")
    print(f"  url:  {GENGHIS_URL}")
    print("=" * 70)

    # Preload Genghis Khan page into the headless browser session.
    import urllib.request
    req = urllib.request.Request(
        "http://localhost:18005/api/browser/session",
        data=json.dumps({"action": "goto", "url": GENGHIS_URL,
                         "wait_until": "domcontentloaded"}).encode(),
        headers={"content-type": "application/json"},
    )
    body = urllib.request.urlopen(req, timeout=25).read()
    print(f"  preload OK ({json.loads(body).get('title', '?')[:60]})")

    patches_a = _patches(fake_speak_a)
    for p in patches_a: p.start()
    try:
        from persona.teacher.triggers import _execute_research
        t0 = time.monotonic()
        await _execute_research(user_id, GENGHIS_GOAL)
        elapsed_a = time.monotonic() - t0
    finally:
        for p in patches_a:
            try: p.stop()
            except Exception: pass

    # Close session between phases
    try:
        req = urllib.request.Request(
            "http://localhost:18005/api/browser/session",
            data=json.dumps({"action": "close"}).encode(),
            headers={"content-type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass

    recipes = list((_REPO / f"data/research/{user_id}/recipes").glob("*.json"))
    print()
    print(f"  elapsed: {elapsed_a:.1f}s")
    print(f"  recipes saved: {len(recipes)}")
    if not recipes:
        print("  FAIL: no recipe recorded — can't proceed to phase B")
        return 1
    recipe_data = json.loads(recipes[0].read_text())
    print(f"  recipe id: {recipe_data['id']}")
    print(f"  recorded refs: {len(recipe_data['recorded_refs'])}")
    print(f"  tool sequence: {len(recipe_data['tool_call_sequence'])} calls")
    if speaks_a:
        print(f"  synthesis A ({len(speaks_a[0]['text'])} chars):")
        print(f"    {speaks_a[0]['text'][:400].replace(chr(10), ' ')}")
    print()

    # ===== PHASE B: REPLAY ATTEMPT =====
    print("=" * 70)
    print(f"PHASE B — REPLAY (different topic, same shape, same host)")
    print(f"  goal: {ARTHUR_GOAL}")
    print(f"  url:  {ARTHUR_URL}")
    print("=" * 70)

    # Reset cache so the lookup re-reads from disk (defensive).
    from workshop.research import recipe_store, recipes as recipes_mod
    recipe_store._invalidate_cache(user_id)

    from infra.rag.embedding import embed_text
    emb_arthur = await embed_text(ARTHUR_GOAL)
    emb_genghis = recipe_data["goal_embedding"]
    sim = _cosine(emb_arthur, emb_genghis)
    print(f"  cosine(arthur, genghis) = {sim:.3f}  (threshold = 0.85)")
    print()

    from workshop.research.recipe_store import lookup as recipe_lookup
    host = recipes_mod.host_from_url(ARTHUR_URL)
    match = await recipe_lookup(user_id, host=host, goal_embedding=emb_arthur, threshold=0.85)

    if match is None:
        print(f"  NO MATCH — recipes don't generalize at threshold 0.85")
        # Try a looser threshold to see how close we got
        loose = await recipe_lookup(user_id, host=host, goal_embedding=emb_arthur, threshold=0.50)
        if loose:
            print(f"  (at threshold 0.50 we WOULD have matched; sim={sim:.3f})")
        print()
        print("VERDICT: NO_REPLAY — recipe didn't generalize.")
        print("(Falling through to fresh Lane R would still answer correctly,")
        print(" but we wouldn't get the speedup we hoped for.)")
        return 0

    print(f"  MATCH! recipe={match.id}  similarity passes 0.85")
    print()

    # Preload King Arthur page; replay against it.
    req = urllib.request.Request(
        "http://localhost:18005/api/browser/session",
        data=json.dumps({"action": "goto", "url": ARTHUR_URL,
                         "wait_until": "domcontentloaded"}).encode(),
        headers={"content-type": "application/json"},
    )
    body = urllib.request.urlopen(req, timeout=25).read()
    print(f"  preload OK ({json.loads(body).get('title', '?')[:60]})")
    print()

    patches_b = _patches(fake_speak_b)
    for p in patches_b: p.start()
    try:
        from persona.teacher.triggers import _execute_research_from_recipe
        t0 = time.monotonic()
        await _execute_research_from_recipe(user_id, ARTHUR_GOAL, ARTHUR_URL, match)
        elapsed_b = time.monotonic() - t0
    finally:
        for p in patches_b:
            try: p.stop()
            except Exception: pass

    print()
    print(f"  elapsed: {elapsed_b:.1f}s")
    if speaks_b:
        print(f"  synthesis B ({len(speaks_b[0]['text'])} chars):")
        print(f"    {speaks_b[0]['text'][:500].replace(chr(10), ' ')}")
    else:
        print("  FAIL: replay produced no synthesis")
        return 1

    # ===== SUMMARY =====
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Phase A (fresh):  {elapsed_a:6.1f}s   {len(speaks_a[0]['text']) if speaks_a else 0} chars")
    print(f"  Phase B (replay): {elapsed_b:6.1f}s   {len(speaks_b[0]['text'])} chars")
    speedup = elapsed_a / elapsed_b if elapsed_b > 0 else float("inf")
    print(f"  Speedup: {speedup:.1f}×")
    print(f"  Cosine sim: {sim:.3f}")
    print()

    # Quick keyword check that the synthesis actually grounds in Arthur
    # content, not Genghis (would indicate a synthesis-from-stale-context bug)
    text_b = (speaks_b[0]["text"] or "").lower()
    grounded_in_arthur = any(k in text_b for k in (
        "arthur", "camelot", "britain", "britons", "celtic", "saxon",
        "round table", "knights", "legend",
    ))
    grounded_in_genghis = any(k in text_b for k in (
        "mongol", "genghis", "khan", "asia", "horde",
    ))
    print(f"  Synthesis grounded in Arthur material: {grounded_in_arthur}")
    print(f"  Synthesis leaked Genghis material:    {grounded_in_genghis}")
    print()
    if grounded_in_arthur and not grounded_in_genghis:
        print("VERDICT: PASS — recipe generalized, synthesis stayed grounded.")
        return 0
    elif grounded_in_genghis and not grounded_in_arthur:
        print("VERDICT: FAIL — synthesis is talking about Genghis Khan even")
        print("though we ran against King Arthur's page. The synthesis prompt")
        print("isn't actually consuming the freshly-collected tool results.")
        return 1
    else:
        print("VERDICT: PARTIAL — mixed grounding. Inspect synthesis above.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
