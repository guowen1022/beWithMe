"""python -m benchmark.model_behavior --region <name>"""

from __future__ import annotations

import argparse
import asyncio
import sys

from benchmark.model_behavior.runner import amain, list_regions


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.model_behavior",
        description="Run an LLM reading-Q&A benchmark for one region.",
    )
    parser.add_argument("--region", help="Region slug (e.g. biology, computer_science).")
    parser.add_argument("--reset", action="store_true", help="Wipe DB before running.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--list", action="store_true", help="List runnable regions and exit."
    )
    args = parser.parse_args()

    regions = list_regions()

    if args.list or not args.region:
        print("Runnable regions:")
        for r in regions:
            print(f"  - {r}")
        if not regions:
            print("  (none — add a questions.yaml with a `sessions:` block)")
        if args.list:
            sys.exit(0)
        if not args.region:
            parser.error("--region is required")

    if args.region not in regions:
        print(
            f"Region '{args.region}' is not runnable.\n"
            f"Available: {', '.join(regions) or '(none)'}",
            file=sys.stderr,
        )
        sys.exit(2)

    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
