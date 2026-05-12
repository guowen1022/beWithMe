"""End-to-end smoke for per-host skill recording + injection.

Two phases:
  A. RECORD: drive `_execute_research` against the Genghis Khan
     Wikipedia page (real LLM, real browser sidecar). Confirm a
     navigation note got written to data/per-host-skills/en.wikipedia.org.md.
  B. INJECT: assemble a NEW research context for the King Arthur
     Wikipedia page (same host, completely different topic, different
     user_id). Inspect the assembled prompt; confirm the saved note
     appears in dynamic_user. Confirm mark_used bumped use_count.

This proves the cross-user, per-host knowledge accumulation actually
works end-to-end. Different from recipe-replay generalization: this
tests the PROSE-NOTE injection path, which augments the LLM's prompt
rather than replacing the loop entirely.

Requires:
  - Isolated worktree browser sidecar on :18005 with the snapshot action.
  - BASE_PORT=18000 env var so `tools.browser_set` resolves there.
  - .env populated with DeepSeek + Doubao + Ollama creds.

Run:
    BASE_PORT=18000 .venv/bin/python tests/manual/test_per_host_skills_e2e.py
"""
from __future__ import annotations

import asyncio
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


# ---- scenario ---------------------------------------------------------

GENGHIS_GOAL = "What's the impact on Europe of Genghis Khan?"
GENGHIS_URL = "https://en.wikipedia.org/wiki/Genghis_Khan"

ARTHUR_GOAL = "What's the impact on Europe of King Arthur?"
ARTHUR_URL = "https://en.wikipedia.org/wiki/King_Arthur"

EXPECTED_HOST = "en.wikipedia.org"


# ---- stubs aligned with the existing replay e2e -----------------------

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


async def main() -> int:
    # Start with a clean per-host-skills directory so we're measuring
    # a true cold-start writethrough, not noise from prior runs.
    skills_root = _REPO / "data" / "per-host-skills"
    if skills_root.exists():
        # Move out of the way rather than delete (so the user can inspect
        # prior runs if they want).
        ts = int(time.time())
        shutil.move(str(skills_root), str(skills_root.parent / f"per-host-skills.before-e2e-{ts}"))

    # Each phase uses a different user_id so we prove the note is GLOBAL
    # (cross-user), not per-user.
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    speaks_a: list[dict] = []

    async def fake_speak_a(*, user_id, text, channel, **kw):
        speaks_a.append({"text": text, "channel": channel})
        return ["test"]

    # ===== PHASE A: RECORD =====
    print("=" * 70)
    print("PHASE A — RECORD")
    print(f"  user: {user_a}")
    print(f"  goal: {GENGHIS_GOAL}")
    print(f"  url:  {GENGHIS_URL}")
    print("=" * 70)

    # Preload the page in the sidecar so the agent's first browser_set
    # call lands on a ready session.
    import urllib.request, json
    req = urllib.request.Request(
        "http://localhost:18005/api/browser/session",
        data=json.dumps({"action": "goto", "url": GENGHIS_URL,
                         "wait_until": "domcontentloaded"}).encode(),
        headers={"content-type": "application/json"},
    )
    body = urllib.request.urlopen(req, timeout=25).read()
    print(f"  preload OK ({json.loads(body).get('title', '?')[:60]})")

    patches_a = _patches(fake_speak_a)
    for p in patches_a:
        p.start()
    try:
        from persona.teacher.triggers import _execute_research
        t0 = time.monotonic()
        await _execute_research(user_a, GENGHIS_GOAL, GENGHIS_URL)
        elapsed_a = time.monotonic() - t0
    finally:
        for p in patches_a:
            try:
                p.stop()
            except Exception:
                pass

    # Close session so phase B's preload starts fresh.
    try:
        req = urllib.request.Request(
            "http://localhost:18005/api/browser/session",
            data=json.dumps({"action": "close"}).encode(),
            headers={"content-type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass

    print()
    print(f"  elapsed: {elapsed_a:.1f}s")
    print(f"  speak calls: {len(speaks_a)}")

    note_path = skills_root / f"{EXPECTED_HOST}.md"
    if not note_path.exists():
        print(f"  FAIL: no per-host skill file written at {note_path}")
        return 1
    raw = note_path.read_text(encoding="utf-8")
    print(f"  note file: {note_path}")
    print(f"  size: {len(raw)} chars")
    # Strip frontmatter for readability
    note_body_idx = raw.find("---\n", 4) + 4
    body_preview = raw[note_body_idx:note_body_idx + 500].strip()
    print(f"  body preview:")
    for ln in body_preview.split("\n")[:8]:
        print(f"    {ln}")
    print()

    # Sanity: the note should look like SITE prose (not topic prose).
    # Heuristic — mentions Wikipedia or some navigation term.
    nav_terms = ["wikipedia", "article", "section", "anchor", "snapshot",
                 "infobox", "read_url", "browser_set", "navigation",
                 "@ref", "@e", "#history", "headings"]
    body_lower = body_preview.lower()
    matched_nav = [t for t in nav_terms if t in body_lower]
    print(f"  navigation-term hits: {matched_nav[:5]} ({len(matched_nav)} total)")
    if not matched_nav:
        # Not a hard failure — the model might have used different
        # terminology — but flag it for human review.
        print("  WARNING: no obvious navigation terms in the note; please eyeball.")

    # ===== PHASE B: INJECT =====
    print("=" * 70)
    print("PHASE B — INJECT (different user, different topic, same host)")
    print(f"  user: {user_b}")
    print(f"  goal: {ARTHUR_GOAL}")
    print(f"  url:  {ARTHUR_URL}")
    print("=" * 70)

    # We don't drive a full research run for B — just assemble the
    # context and verify the saved note is in the prompt.
    patches_b = _patches(lambda **kw: asyncio.sleep(0))  # speak isn't called by assemble
    for p in patches_b:
        p.start()
    try:
        from persona.teacher.contexts.research import assemble as assemble_research
        from workshop.research import per_host_skills

        skill_before = await per_host_skills.get(EXPECTED_HOST)
        use_count_before = skill_before.use_count if skill_before else 0

        ctx = await assemble_research(user_b, ARTHUR_GOAL, goal_url=ARTHUR_URL)
        dyn = ctx.parts.dynamic_user or ""

        skill_after = await per_host_skills.get(EXPECTED_HOST)
        use_count_after = skill_after.use_count if skill_after else 0
    finally:
        for p in patches_b:
            try:
                p.stop()
            except Exception:
                pass

    print()
    expected_marker = f"=== KNOWN NOTES FOR THIS SITE ({EXPECTED_HOST}) ==="
    has_marker = expected_marker in dyn
    print(f"  prompt size: {len(dyn)} chars")
    print(f"  contains KNOWN-NOTES section: {has_marker}")

    print(f"  use_count: {use_count_before} → {use_count_after}")
    use_count_bumped = use_count_after == use_count_before + 1

    if not has_marker:
        print("  FAIL: per-host note was NOT injected into dynamic_user")
        return 1
    if not use_count_bumped:
        print(f"  FAIL: use_count did not bump by 1 ({use_count_before} → {use_count_after})")
        return 1

    # Show the injected note in-prompt
    idx = dyn.find(expected_marker)
    section_end = dyn.find("=== RESEARCH GOAL ===", idx)
    injected = dyn[idx:section_end].strip()
    print()
    print(f"  Injected section ({len(injected)} chars):")
    for ln in injected.split("\n")[:10]:
        print(f"    {ln[:100]}")
    print()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Phase A elapsed: {elapsed_a:.1f}s")
    print(f"  Note saved at:   {note_path}")
    print(f"  Phase B users:   A={str(user_a)[:8]}…  B={str(user_b)[:8]}…  (different)")
    print(f"  Note injected into B's prompt: ✓")
    print(f"  use_count bumped: ✓ ({use_count_before} → {use_count_after})")
    print()
    print("VERDICT: PASS — per-host skill recorded by user A, injected into user B's prompt.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
