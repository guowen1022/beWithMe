"""Allow running as: python -m benchmark"""
from benchmark.runner import main
import asyncio

asyncio.run(main())
