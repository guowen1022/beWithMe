"""python -m benchmark.goal_planning --topic <slug>"""

from __future__ import annotations

import argparse
import asyncio
import sys

from benchmark.goal_planning.runner import amain, list_topics


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.goal_planning",
        description="Run a goal-planning behavior benchmark for one topic.",
    )
    parser.add_argument("--topic", help="Topic slug (e.g. learn-web-dev).")
    parser.add_argument("--reset", action="store_true", help="Wipe DB before running.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--list", action="store_true", help="List runnable topics and exit."
    )
    args = parser.parse_args()

    topics = list_topics()

    if args.list or not args.topic:
        print("Runnable topics:")
        for t in topics:
            print(f"  - {t}")
        if not topics:
            print("  (none — add a questions.yaml with a `goal:` field)")
        if args.list:
            sys.exit(0)
        if not args.topic:
            parser.error("--topic is required")

    if args.topic not in topics:
        print(
            f"Topic '{args.topic}' is not runnable.\n"
            f"Available: {', '.join(topics) or '(none)'}",
            file=sys.stderr,
        )
        sys.exit(2)

    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
