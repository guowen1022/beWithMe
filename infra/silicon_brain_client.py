"""Typed HTTP client for the silicon_brain (knowledge sidecar).

silicon_brain holds neutral user data only — Profile (self_description),
UserPreferences (user-stated), Documents/DocumentChunks. Personas read
these via this narrow client. Persona-authored data (Interactions,
ConceptNodes, Recommendations, LearningSessions, etc.) is stored in the
persona's own tables and read directly via SQLAlchemy on `infra.db`, not
over HTTP.

Lives in `infra/` because every persona shares it; nothing here is
teacher-specific.

Auth: every call auto-injects `X-User-Id`.
Network: `trust_env=False` so a system HTTP_PROXY doesn't intercept localhost
loopbacks.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import httpx

from infra.contracts import DocumentChunkDTO, NoteHitDTO, ProfileDTO, UserProfileDTO
from infra.contracts.event import EventDTO, EventEmit, StreamQuery
from infra.contracts.inbox import InboxProposalCreate, InboxProposalDTO
from infra.contracts.feed import (
    FeedCandidateCreate,
    FeedCandidateDTO,
    FeedCandidateReplace,
)
from infra.topology import upstream_url


def _user_headers(user_id: UUID, extra: Optional[dict] = None) -> dict:
    h = {"X-User-Id": str(user_id)}
    if extra:
        h.update(extra)
    return h


class SiliconBrainClient:
    """Async HTTP client for the knowledge sidecar.

    Narrow surface: only neutral user-data reads. Teacher's own data is
    queried directly via `infra.db` and never goes through this client.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self._base = (base_url or upstream_url("knowledge")).rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base, timeout=timeout, trust_env=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_profile(self, user_id: UUID) -> ProfileDTO:
        """User's Profile (self_description). Neutral user data."""
        resp = await self._http.get("/api/profile", headers=_user_headers(user_id))
        resp.raise_for_status()
        # The /api/profile route returns ProfileRead shape (self_description, created_at).
        # Return ProfileDTO with user_id filled in.
        data = resp.json()
        return ProfileDTO(
            user_id=user_id,
            self_description=data.get("self_description", ""),
            created_at=data.get("created_at"),
        )

    async def get_user_preferences(self, user_id: UUID) -> UserProfileDTO:
        """User's UserPreferences — what the user explicitly stated."""
        resp = await self._http.get("/api/preferences", headers=_user_headers(user_id))
        resp.raise_for_status()
        return UserProfileDTO.model_validate(resp.json())

    async def get_talk_preference(self, user_id: UUID) -> dict:
        """Per-device talk-channel preference.

        Returns `{"desktop": ..., "tablet": ..., "phone": ...}`, each
        value one of `voice` | `text` | `both`. The defaults
        (desktop=both, tablet=both, phone=text) are applied server-side
        on first read.
        """
        resp = await self._http.get("/api/talk-preference", headers=_user_headers(user_id))
        resp.raise_for_status()
        return resp.json()

    async def search_document_chunks(
        self,
        user_id: UUID,
        document_id: UUID,
        query_embedding: list[float],
        *,
        top_k: int = 5,
    ) -> list[DocumentChunkDTO]:
        resp = await self._http.post(
            "/api/retrieval/document-chunks",
            headers=_user_headers(user_id),
            json={
                "document_id": str(document_id),
                "query_embedding": query_embedding,
                "top_k": top_k,
            },
        )
        resp.raise_for_status()
        return [DocumentChunkDTO.model_validate(x) for x in resp.json()]

    async def get_document_structure(
        self, user_id: UUID, document_id: UUID,
    ) -> dict:
        """Return `{title, page_count, outline}` for a document.

        Backfills outline + page_count on the silicon_brain side if the doc
        was uploaded before per-page chunking. Cheap (one row, no chunks).
        """
        resp = await self._http.get(
            f"/api/documents/{document_id}/structure",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return resp.json()

    async def search_notes(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[NoteHitDTO]:
        """Find note chunks semantically related to `query`. The knowledge
        sidecar applies nomic's `search_query:` task prefix internally."""
        resp = await self._http.get(
            "/api/notes/search",
            headers=_user_headers(user_id),
            params={"q": query, "top_k": top_k},
        )
        resp.raise_for_status()
        return [NoteHitDTO.model_validate(x) for x in resp.json()]

    async def get_document_page(
        self, user_id: UUID, document_id: UUID, page_number: int,
    ) -> dict:
        """Return `{page_number, text}` — full text of one page.

        Concatenates page-tagged chunks in chunk_index order. For legacy
        docs without page_number on chunks, silicon_brain re-extracts
        from `pdf_data` server-side.
        """
        resp = await self._http.get(
            f"/api/documents/{document_id}/pages/{page_number}",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return resp.json()

    # --- Event stream (SPEC §8) ---

    async def emit_event(self, user_id: UUID, emit: EventEmit) -> EventDTO:
        """Append one event to the user's stream. Returns the persisted row."""
        resp = await self._http.post(
            "/api/event-stream",
            headers=_user_headers(user_id),
            json=emit.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return EventDTO.model_validate(resp.json())

    async def query_stream(self, user_id: UUID, q: StreamQuery) -> list[EventDTO]:
        """List events for this user matching the filter. `q.order` defaults to desc."""
        resp = await self._http.post(
            "/api/event-stream/query",
            headers=_user_headers(user_id),
            json=q.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return [EventDTO.model_validate(x) for x in resp.json()]

    async def read_projection(self, user_id: UUID, name: str) -> dict:
        """Return a Phase-0 projection (SPEC §8.4) as a JSON-able dict.

        Stub projections respond `{"_stub": True, "name": "<name>"}` until
        the PR that implements them lands.
        """
        resp = await self._http.get(
            f"/api/event-stream/projections/{name}",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return resp.json()

    async def read_view(self, user_id: UUID, name: str) -> list[dict]:
        """Return a Phase-0 view (SPEC §15.4) as a list of dicts.

        Views are chronological/log-shaped reads over the stream — the
        companion to projections (which return state snapshots).
        """
        resp = await self._http.get(
            f"/api/event-stream/views/{name}",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return resp.json()

    # --- Inbox proposals (PR-5) ---

    async def write_inbox_proposal(
        self, user_id: UUID, proposal: InboxProposalCreate,
    ) -> InboxProposalDTO:
        resp = await self._http.post(
            "/api/inbox",
            headers=_user_headers(user_id),
            json=proposal.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return InboxProposalDTO.model_validate(resp.json())

    async def list_inbox_proposals(
        self, user_id: UUID, *, status: Optional[str] = None, limit: int = 50,
    ) -> list[InboxProposalDTO]:
        params: dict = {"limit": limit}
        if status is not None:
            params["status"] = status
        resp = await self._http.get(
            "/api/inbox",
            headers=_user_headers(user_id),
            params=params,
        )
        resp.raise_for_status()
        return [InboxProposalDTO.model_validate(x) for x in resp.json()]

    async def tap_inbox_proposal(
        self, user_id: UUID, proposal_id: UUID,
    ) -> InboxProposalDTO:
        resp = await self._http.post(
            f"/api/inbox/{proposal_id}/tap",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return InboxProposalDTO.model_validate(resp.json())

    async def dismiss_inbox_proposal(
        self, user_id: UUID, proposal_id: UUID,
    ) -> InboxProposalDTO:
        resp = await self._http.post(
            f"/api/inbox/{proposal_id}/dismiss",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return InboxProposalDTO.model_validate(resp.json())

    async def consume_inbox_proposal(
        self, user_id: UUID, proposal_id: UUID,
    ) -> InboxProposalDTO:
        resp = await self._http.post(
            f"/api/inbox/{proposal_id}/consume",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return InboxProposalDTO.model_validate(resp.json())

    # --- Feed candidates (multi-persona feed store) ---

    async def write_feed_candidate(
        self, user_id: UUID, candidate: FeedCandidateCreate,
    ) -> FeedCandidateDTO:
        resp = await self._http.post(
            "/api/feed-candidates",
            headers=_user_headers(user_id),
            json=candidate.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return FeedCandidateDTO.model_validate(resp.json())

    async def replace_feed_candidates(
        self, user_id: UUID, source_persona: str,
        items: list[FeedCandidateCreate],
    ) -> list[FeedCandidateDTO]:
        body = FeedCandidateReplace(source_persona=source_persona, items=items)
        resp = await self._http.post(
            "/api/feed-candidates/replace",
            headers=_user_headers(user_id),
            json=body.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return [FeedCandidateDTO.model_validate(x) for x in resp.json()]

    async def list_feed_candidates(
        self, user_id: UUID, *,
        status: Optional[str] = None,
        source_persona: Optional[str] = None,
        limit: int = 50,
    ) -> list[FeedCandidateDTO]:
        params: dict = {"limit": limit}
        if status is not None:
            params["status"] = status
        if source_persona is not None:
            params["source_persona"] = source_persona
        resp = await self._http.get(
            "/api/feed-candidates",
            headers=_user_headers(user_id),
            params=params,
        )
        resp.raise_for_status()
        return [FeedCandidateDTO.model_validate(x) for x in resp.json()]

    async def select_feed_candidate(
        self, user_id: UUID, candidate_id: UUID,
    ) -> FeedCandidateDTO:
        resp = await self._http.post(
            f"/api/feed-candidates/{candidate_id}/select",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return FeedCandidateDTO.model_validate(resp.json())

    async def dismiss_feed_candidate(
        self, user_id: UUID, candidate_id: UUID,
    ) -> FeedCandidateDTO:
        resp = await self._http.post(
            f"/api/feed-candidates/{candidate_id}/dismiss",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return FeedCandidateDTO.model_validate(resp.json())
