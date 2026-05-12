"""Run as: python -m benchmark.research [--scenario N]"""
import asyncio
import sys

from benchmark.research.runner import main


sys.exit(asyncio.run(main()))
