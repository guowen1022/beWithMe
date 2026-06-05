"""stream_query — read raw events from the learner's stream.

Companion to `stream_emit`. Returns the ground-truth event rows
matching simple filters (kind, source, time window). Use this when a
projection isn't enough — e.g. "show me every `agent.observation` from
this week so I can decide whether to supersede one of them."

For aggregated views over the stream, prefer `read_projection` — it's
cheaper to reason about and lives behind a named contract.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from infra.contracts.event import StreamQuery
from infra.model.tools import ToolSpec
from infra.silicon_brain_client import SiliconBrainClient


async def stream_query(
    *,
    user_id: UUID,
    kinds: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 20,
    order: str = "desc",
) -> Dict[str, Any]:
    if order not in ("asc", "desc"):
        return {"error": "order must be 'asc' or 'desc'"}
    limit = max(1, min(100, int(limit)))

    q = StreamQuery(
        kinds=kinds,
        sources=sources,
        since=since,
        until=until,
        limit=limit,
        order=order,
    )

    client = SiliconBrainClient()
    try:
        try:
            rows = await client.query_stream(user_id, q)
        except Exception as e:
            return {"error": f"query failed: {e}"}
        return {
            "count": len(rows),
            "events": [
                {
                    "event_id": str(r.event_id),
                    "ts": r.ts.isoformat(),
                    "valid_at": r.valid_at.isoformat() if r.valid_at else None,
                    "source": r.source,
                    "kind": r.kind,
                    "body": r.body,
                    "refs": r.refs,
                }
                for r in rows
            ],
        }
    finally:
        await client.aclose()


def _make_stream_query(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        kinds = args.get("kinds")
        if kinds is not None and not (isinstance(kinds, list) and all(isinstance(k, str) for k in kinds)):
            return json.dumps({"error": "kinds must be a list of strings"})
        sources = args.get("sources")
        if sources is not None and not (isinstance(sources, list) and all(isinstance(s, str) for s in sources)):
            return json.dumps({"error": "sources must be a list of strings"})
        since = args.get("since")
        until = args.get("until")
        limit_raw = args.get("limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else 20
        except (TypeError, ValueError):
            return json.dumps({"error": "limit must be an integer"})
        order = args.get("order") or "desc"
        result = await stream_query(
            user_id=user_id,
            kinds=kinds,
            sources=sources,
            since=since,
            until=until,
            limit=limit,
            order=order,
        )
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="stream_query",
        description=(
            "Read raw events from this learner's stream. Use when you "
            "need to look back at what you (or the Maestro) recorded — "
            "to decide whether to supersede, to ground a follow-up in "
            "what was already observed, or to confirm a pattern. "
            "Prefer `read_projection` for aggregated views; only call "
            "this when the projection's shape doesn't cover what you "
            "need.\n"
            "\n"
            "All filters are AND-ed. Returns the most recent matches "
            "first by default; flip `order` for chronological. Each "
            "event carries `event_id` (use in `refs.supersedes`), "
            "`ts`, `valid_at`, `source`, `kind`, `body`, `refs`. "
            "Capped at 100 rows per call to keep context bounded."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by event kind. e.g. "
                        "['agent.observation'] or ['user.engagement_started', "
                        "'user.engagement_ended']."
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by source: user / agent / maestro_long / maestro_short / signal / capture / system.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO-8601 timestamp. Events with `ts >= since`.",
                },
                "until": {
                    "type": "string",
                    "description": "ISO-8601 timestamp. Events with `ts < until` (exclusive).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max events to return. Default 20.",
                },
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Sort by `ts`. Default 'desc' (newest first).",
                },
            },
            "additionalProperties": False,
        },
        executor=_make_stream_query(user_id),
    )


__all__ = ["stream_query", "build_spec"]
