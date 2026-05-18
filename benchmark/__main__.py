"""Allow running as: python -m benchmark

DEPRECATED. Prefer the YAML-driven sub-packages:
    python -m benchmark.model_behavior --region <name>
    python -m benchmark.goal_planning --topic <slug>
This shim is kept only for in-flight callers of the old flag-based CLI.
"""
from benchmark.runner import main
import asyncio

asyncio.run(main())
