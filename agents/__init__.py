"""Agents — non-client-facing LLM workers.

Personas face the user. Agents don't. An agent is invoked by a tool when a
persona needs work done that doesn't belong on the persona's own decision
loop (writing UI code, doing a deep web crawl, transforming a document).

Agents may be called in-process from tools. If an agent later needs
isolation, scaling, or a sandbox, promote it to a sidecar — the tool is the
seam, so callers don't notice.
"""
