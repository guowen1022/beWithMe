"""Research-mode benchmark — multi-step investigation scenarios.

Each scenario:
  - preloads a URL into the running browser sidecar
  - drives `persona.teacher.triggers._execute_research` against it
  - records the agent's plan, notes, synthesis, tool calls, wall time
  - scores against an explicit `expected_procedure` (what the plan
    should cover) and `expected_result` (what the synthesis must
    contain)

Run:
    python -m benchmark.research              # all scenarios
    python -m benchmark.research --scenario 2 # one
"""
