"""Unit smoke for workshop/research/per_host_skills.

Tests the file store, safe-host validation, LLM-mediated merge (with
a stub LLM), and the no-op-on-redundant-merge path. No real LLM, no
network.

Run:
    .venv/bin/python tests/manual/test_per_host_skills.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("LLM_PROVIDER", "deepseek")
os.environ.setdefault("DEEPSEEK_API_KEY", "x")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://x")
os.environ.setdefault("DEEPSEEK_MODEL", "x")
os.environ.setdefault("VISION_PROVIDER", "doubao")
os.environ.setdefault("DOUBAO_API_KEY", "x")
os.environ.setdefault("DOUBAO_BASE_URL", "https://x")
os.environ.setdefault("DOUBAO_VISION_MODEL", "x")


from workshop.research import per_host_skills  # noqa: E402


async def main() -> int:
    tmp = Path("/tmp/bewithme-per-host-test")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # Redirect the data root for this test.
    per_host_skills._DATA_ROOT = tmp

    # ===== 1. Fresh save (no prior file) =====
    s1 = await per_host_skills.save(
        "en.wikipedia.org",
        "Wikipedia article infoboxes hold the key facts in a sidebar; snapshot+text @e<n> on the infobox heading is fastest.",
    )
    assert s1 is not None
    assert s1.host == "en.wikipedia.org"
    assert "infobox" in s1.note
    assert s1.use_count == 0
    print(f"[1] fresh save OK ({len(s1.note)} chars)")

    # ===== 2. NO_NOTE is a no-op =====
    s2 = await per_host_skills.save("en.wikipedia.org", "NO_NOTE")
    assert s2 is None
    # File on disk should be unchanged
    stored = await per_host_skills.get("en.wikipedia.org")
    assert stored is not None
    assert stored.note == s1.note
    print("[2] NO_NOTE no-op OK")

    # ===== 3. Empty string ignored =====
    s3 = await per_host_skills.save("en.wikipedia.org", "   ")
    assert s3 is None
    print("[3] empty save no-op OK")

    # ===== 4. Save with merge — stub the LLM =====
    async def stub_merge_returns_combined(host, existing_note, new_paragraph):
        # Pretend the LLM returns a cleanly merged note.
        return f"{existing_note}\n\nMERGED: {new_paragraph[:80]}"

    with patch.object(per_host_skills, "_llm_merge", stub_merge_returns_combined):
        s4 = await per_host_skills.save(
            "en.wikipedia.org",
            "Long articles get truncated by read_url at 12 KB; use the #anchor URLs to fetch each section separately.",
        )
    assert s4 is not None
    assert "infobox" in s4.note          # original kept
    assert "MERGED:" in s4.note          # merge fired
    assert s4.use_count == 0             # merge doesn't bump use_count
    assert s4.updated_at >= s1.created_at
    print(f"[4] LLM-merge save OK ({len(s4.note)} chars)")

    # ===== 5. mark_used bumps use_count =====
    await per_host_skills.mark_used("en.wikipedia.org")
    after = await per_host_skills.get("en.wikipedia.org")
    assert after.use_count == 1
    await per_host_skills.mark_used("en.wikipedia.org")
    after2 = await per_host_skills.get("en.wikipedia.org")
    assert after2.use_count == 2
    print(f"[5] mark_used OK ({after2.use_count=})")

    # ===== 6. mark_used on missing host is a no-op =====
    await per_host_skills.mark_used("nonexistent.example.com")
    assert await per_host_skills.get("nonexistent.example.com") is None
    print("[6] mark_used on missing host no-op OK")

    # ===== 7. Unsafe host rejected =====
    bad = await per_host_skills.save("../../etc/passwd", "evil note")
    assert bad is None
    # No file should have been created outside our data root
    assert not (tmp.parent / "passwd").exists()
    assert not list(tmp.glob("**/passwd*"))
    print("[7] unsafe host rejected OK")

    # ===== 8. List + delete =====
    # Add a second host
    s_other = await per_host_skills.save(
        "finance.google.com",
        "Key-Stats anchor has price + 52-week range; news section needs scroll.",
    )
    assert s_other is not None

    all_skills = await per_host_skills.list_all()
    hosts = sorted(s.host for s in all_skills)
    assert hosts == ["en.wikipedia.org", "finance.google.com"], hosts
    print(f"[8] list_all OK ({len(all_skills)} hosts)")

    await per_host_skills.delete("finance.google.com")
    assert await per_host_skills.get("finance.google.com") is None
    remaining = await per_host_skills.list_all()
    assert len(remaining) == 1
    print("[9] delete OK")

    # ===== 10. No-op merge: LLM returns the existing note verbatim =====
    async def stub_merge_returns_existing(host, existing_note, new_paragraph):
        return existing_note

    before_update = (await per_host_skills.get("en.wikipedia.org")).updated_at
    with patch.object(per_host_skills, "_llm_merge", stub_merge_returns_existing):
        s_noop = await per_host_skills.save(
            "en.wikipedia.org",
            "Some redundant observation that's already covered.",
        )
    after_update = (await per_host_skills.get("en.wikipedia.org")).updated_at
    # When the merge yields the existing note verbatim, updated_at is NOT bumped
    assert s_noop is not None
    assert after_update == before_update
    print("[10] no-op merge skips disk write OK")

    # ===== 11. Frontmatter parse round-trip =====
    raw = (tmp / "en.wikipedia.org.md").read_text()
    assert raw.startswith("---\n")
    assert "host: en.wikipedia.org" in raw
    assert "use_count: 2" in raw  # last bumped to 2 in step 5
    parsed = per_host_skills.PerHostSkill.from_markdown(raw, fallback_host="x")
    assert parsed.host == "en.wikipedia.org"
    assert parsed.use_count == 2
    print("[11] frontmatter round-trip OK")

    print()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
