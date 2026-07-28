#!/usr/bin/env python3
"""Per-service boot smoke test -- "does this sidecar actually come up?".

Importing `services.<name>.main` exercises the whole dependency chain for that
one service: its own module tree, the infra/silicon_brain/persona code it pulls
in, and every third-party package those need. A missing wheel, a circular
import, or a module-level `raise` (see infra/model/llm.py:45) fails here.

That matters because unit-test coverage is uneven -- shell, speak and browser
have no tests of their own, so without this a broken import in one of them
reaches production unnoticed.

What this does NOT do: bind a port, reach Postgres/Ollama, or load model
artifacts. It is a static boot check, deliberately hermetic so it runs in CI
with no services alongside it.

Usage:
    python scripts/smoke_service.py knowledge
    python scripts/smoke_service.py --all
"""
from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path

# Running this as `python scripts/smoke_service.py` puts scripts/ on sys.path,
# not the repo root, so `services.*` would not resolve. Prepend the root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Keep in sync with infra/topology.py:SERVICE_OFFSETS.
SERVICES = ["shell", "persona", "knowledge", "transcribe", "speak", "browser", "maestro", "tuning"]


def smoke(name: str) -> bool:
    """Import the sidecar and assert it exposes a mounted ASGI app."""
    module_path = f"services.{name}.main"
    try:
        module = importlib.import_module(module_path)
    except Exception:
        print(f"FAIL: {name}: could not import {module_path}", file=sys.stderr)
        traceback.print_exc()
        return False

    app = getattr(module, "app", None)
    if app is None:
        print(f"FAIL: {name}: {module_path} defines no `app`", file=sys.stderr)
        return False

    # Every sidecar is a FastAPI/Starlette app; `routes` is the cheapest proof
    # the object is really mounted rather than a stray attribute.
    routes = getattr(app, "routes", None)
    if routes is None:
        print(f"FAIL: {name}: `app` has no routes attribute (got {type(app).__name__})", file=sys.stderr)
        return False

    title = getattr(app, "title", "?")
    print(f"OK: {name}: {module_path} imported, {len(routes)} routes, title={title!r}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", nargs="?", choices=SERVICES, help="service to smoke test")
    parser.add_argument("--all", action="store_true", help="smoke every service in turn")
    args = parser.parse_args()

    if not args.all and not args.service:
        parser.error("give a service name or --all")

    targets = SERVICES if args.all else [args.service]
    failed = [name for name in targets if not smoke(name)]

    if failed:
        print(f"\nFAIL: {len(failed)}/{len(targets)} service(s) failed to boot: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"\nOK: all {len(targets)} service(s) boot cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
