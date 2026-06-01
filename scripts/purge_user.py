"""Show or erase everything stored about one user — the operator entry point.

The user-data map (``infra/user_data.py``) is the source of truth for *where* a
user's data lives across every domain. This script is just a thin driver over
it. There is intentionally no service/endpoint wiring yet — a user-initiated
"delete my account" flow can call the same ``infra.user_data`` functions later.

Usage:
    python scripts/purge_user.py --user <uuid>             # dry run: print the map
    python scripts/purge_user.py --user <uuid> --confirm   # actually erase

Resolve a username to its id first if needed:
    python scripts/purge_user.py --username alice          # dry run by name
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from infra.db import async_session
from infra.user_data import describe_user_data, load_domains, purge_user_data


def _print_map(data: dict) -> None:
    print(f"\nUser {data['user_id']}")
    print("\n  Database tables:")
    for t in data["tables"]:
        mark = " " if t["rows"] == 0 else "*"
        print(f"   {mark} {t['table']:<28} {t['rows']:>6} rows")
    print("\n  Disk directories:")
    for d in data["dirs"]:
        if d["exists"]:
            print(f"   * {d['name']:<14} {d['files']:>4} files  {d['bytes']/1024:.1f}KB  {d['path']}")
        else:
            print(f"     {d['name']:<14}    (none)  {d['path']}")
    print(f"\n  Totals: {data['total_rows']} rows, {data['total_files']} files, {data['total_bytes']/1024:.1f}KB\n")


async def _resolve_username(session, username: str) -> UUID | None:
    # Imported lazily; load_domains() has already registered the User model.
    from silicon_brain.models.user import User
    return await session.scalar(select(User.id).where(User.username == username))


async def _run(args: argparse.Namespace) -> int:
    load_domains()  # populate Base.metadata + the disk registry

    async with async_session() as session:
        if args.username:
            uid = await _resolve_username(session, args.username)
            if uid is None:
                print(f"No user with username {args.username!r}", file=sys.stderr)
                return 1
        else:
            uid = UUID(args.user)

        data = await describe_user_data(session, uid)
        _print_map(data)

        if not args.confirm:
            print("Dry run. Re-run with --confirm to erase the above.")
            return 0

        result = await purge_user_data(session, uid)
        await session.commit()

    print(f"Erased {result['total_rows']} rows across {len(result['deleted_rows'])} tables.")
    print(f"Removed {len(result['removed_dirs'])} directories.")
    for err in result["errors"]:
        print(f"  ! {err}", file=sys.stderr)
    return 1 if result["errors"] else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Show or erase all data for one user.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--user", help="user id (UUID)")
    g.add_argument("--username", help="resolve user by username instead of id")
    p.add_argument("--confirm", action="store_true", help="actually erase (default: dry run)")
    raise SystemExit(asyncio.run(_run(p.parse_args())))


if __name__ == "__main__":
    main()
