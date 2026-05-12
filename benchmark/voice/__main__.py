"""Allow running as: python -m benchmark.voice"""
import asyncio

from benchmark.voice.runner import main

asyncio.run(main())
