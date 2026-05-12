"""Tool-orchestration accuracy benchmark.

Measures whether the persona picks the right tool (and reasonable
arguments) for a given user intent. Runs each scenario twice — once
with Lane A's default `disable_thinking=True` and once with thinking
on — so we can quantify the orchestration trade-off we accepted when
turning thinking off for fast voice replies.
"""
