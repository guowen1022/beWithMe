"""Allow running as: python -m benchmark.orchestration"""
import asyncio

from benchmark.orchestration.runner import main

asyncio.run(main())
