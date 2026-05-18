"""python -m benchmark.file_understanding --slug <name>"""

from __future__ import annotations

import argparse
import asyncio
import sys

from benchmark.file_understanding.runner import amain, list_slugs


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.file_understanding",
        description="Run an LLM file-Q&A benchmark (PDF / video / image / audio).",
    )
    parser.add_argument("--slug", help="Slug folder name (e.g. gettysburg-pdf).")
    parser.add_argument("--reset", action="store_true", help="Wipe DB before running.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--list", action="store_true", help="List runnable slugs and exit."
    )
    args = parser.parse_args()

    slugs = list_slugs()

    if args.list or not args.slug:
        print("Runnable slugs:")
        for s in slugs:
            print(f"  - {s}")
        if not slugs:
            print("  (none — add a questions.yaml with `questions:` + `file:` blocks)")
        if args.list:
            sys.exit(0)
        if not args.slug:
            parser.error("--slug is required")

    if args.slug not in slugs:
        print(
            f"Slug '{args.slug}' is not runnable.\n"
            f"Available: {', '.join(slugs) or '(none)'}",
            file=sys.stderr,
        )
        sys.exit(2)

    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
