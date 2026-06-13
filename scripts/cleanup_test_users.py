"""Delete users created by the e2e test suite (and other test fixtures).

Default targets the obvious test-fixture patterns:
  e2e-test-*, e2e-mt-*, public-create-*, voice-test-*

These are short-lived users created during pytest runs and ad-hoc probes.
Their data (interactions, concepts, sessions, etc.) cascades automatically
via ON DELETE CASCADE on the user_id foreign keys.

Also wipes each deleted user's per-user-git canvas workspace under
`data/canvases/<uuid>/` so a fresh user with the same UUID would start
clean (defensive — UUIDs don't realistically collide).

Real users and benchmark users are preserved by default. Use --include-bench
to also wipe `bench_*` users; --include-default to wipe `00000000-...` too.

Usage:
    .venv/bin/python scripts/cleanup_test_users.py            # dry-run by default
    .venv/bin/python scripts/cleanup_test_users.py --apply    # actually delete
    .venv/bin/python scripts/cleanup_test_users.py --apply --include-bench
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

# Make project root importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import asyncpg

from infra.config import settings


# Patterns we treat as test users by default.
TEST_PATTERNS = (
    "e2e-%",            # all e2e fixtures (inbox, engagement, iact, maestro, …)
    "debug-eng-%",      # ad-hoc engagement debug probes
    "spine-%",          # leftover spine probes
    "browse-phase0-%",  # leftover browse probes
    "public-create-%",
    "voice-test-%",
)
BENCH_PATTERN = "bench_%"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
CANVASES_ROOT = _REPO_ROOT / "data" / "canvases"


def _asyncpg_url(sa_url: str) -> str:
    """Strip the SQLAlchemy `+asyncpg` dialect prefix for raw asyncpg."""
    return sa_url.replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool, include_bench: bool, include_default: bool) -> None:
    url = _asyncpg_url(settings.database_url)
    conn = await asyncpg.connect(url)
    try:
        patterns = list(TEST_PATTERNS)
        if include_bench:
            patterns.append(BENCH_PATTERN)

        # Build a single OR'd LIKE query
        like_clauses = " OR ".join(f"username LIKE ${i+1}" for i in range(len(patterns)))
        params = list(patterns)

        if not include_default:
            like_clauses = f"({like_clauses}) AND id != '{DEFAULT_USER_ID}'::uuid"

        rows = await conn.fetch(
            f"SELECT id, username, created_at FROM users WHERE {like_clauses} "
            f"ORDER BY created_at",
            *params,
        )
        if not rows:
            print("No matching test users found.")
            return

        print(f"{'Would delete' if not apply else 'Deleting'} {len(rows)} test users:")
        for r in rows:
            print(f"  {r['id']}  {r['username']:30s}  ({r['created_at']:%Y-%m-%d %H:%M})")

        if not apply:
            print("\n(dry run — pass --apply to actually delete)")
            return

        ids = [r["id"] for r in rows]
        # Single DELETE with ANY($1::uuid[]) — cascades take care of related rows.
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM users WHERE id = ANY($1::uuid[]) RETURNING 1) "
            "SELECT count(*) FROM d",
            ids,
        )
        print(f"\nDeleted {deleted} users (related rows cascaded).")

        # Wipe per-user-git canvas workspaces. The DB cascade doesn't touch
        # the on-disk git repos.
        wiped = 0
        for uid in ids:
            ws_path = CANVASES_ROOT / str(uid)
            if ws_path.is_dir():
                shutil.rmtree(ws_path, ignore_errors=True)
                wiped += 1
        if wiped:
            print(f"Wiped {wiped} canvas workspaces under {CANVASES_ROOT}.")
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default: dry run)")
    p.add_argument("--include-bench", action="store_true",
                   help="also delete `bench_*` users (benchmark fixtures)")
    p.add_argument("--include-default", action="store_true",
                   help="also delete the default '00000000-...' user")
    args = p.parse_args()

    asyncio.run(main(args.apply, args.include_bench, args.include_default))
