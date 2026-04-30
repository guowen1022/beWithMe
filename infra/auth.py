"""Stateless auth dep — used by every sidecar route.

Trust model: the **shell** verifies X-User-Id once per user against the
knowledge sidecar's `/api/auth/verify` endpoint, then forwards. Sidecars
trust whatever the shell sends and never re-check the DB. They MUST only
be reachable on the same private network as the shell.

The DB-backed verification logic lives in the knowledge sidecar
(`services/knowledge/routers/auth.py`) — that's the source of truth the
shell consults. Keeping it out of `infra/` preserves the dep graph:
`infra → silicon_brain` would be a cycle.
"""
from uuid import UUID
from fastapi import Header


async def parse_user_id(x_user_id: UUID = Header(...)) -> UUID:
    """Return the UUID parsed from X-User-Id. No DB check — the shell
    already verified the user before forwarding."""
    return x_user_id
