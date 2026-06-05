"""CLI: `python -m maestro_training replay --user <uuid>` prints Phase-0
baseline metrics for the given user's logged kickoff decisions.

Other subcommands (train, eval, promote) land in PR-8 follow-ups.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from infra.db import async_session
from maestro_training.replay import replay_user, summarise


async def _replay(user_id: UUID) -> dict:
    async with async_session() as db:
        records = await replay_user(db, user_id)
    s = summarise(records)
    return {
        "user_id": str(user_id),
        "total_decisions": s.total_decisions,
        "silence_count": s.silence_count,
        "act_count": s.act_count,
        "tap_rate": round(s.tap_rate, 3),
        "dismiss_rate": round(s.dismiss_rate, 3),
        "expire_rate": round(s.expire_rate, 3),
        "silence_share": round(s.silence_share, 3),
        "outcome_counts": s.outcome_counts,
        "propensity_buckets": s.propensity_buckets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="maestro_training")
    sub = parser.add_subparsers(dest="cmd", required=True)
    replay_p = sub.add_parser("replay", help="Replay one user's kickoff log")
    replay_p.add_argument("--user", required=True, help="User UUID")
    args = parser.parse_args()

    if args.cmd == "replay":
        try:
            uid = UUID(args.user)
        except ValueError:
            parser.error("--user must be a UUID")
        out = asyncio.run(_replay(uid))
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
