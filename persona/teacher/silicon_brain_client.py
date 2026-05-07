"""Typed HTTP client for the silicon_brain (knowledge sidecar).

silicon_brain holds neutral user data only — Profile (self_description),
UserPreferences (user-stated), Documents/DocumentChunks. The teacher reads
these via this narrow client. Everything teacher-authored (Interactions,
ConceptNodes, Recommendations, LearningSessions, TeacherPreferenceModel) is
stored in teacher's own tables and read directly via SQLAlchemy on
`infra.db`, not over HTTP.

Auth: every call auto-injects `X-User-Id`.
Network: `trust_env=False` so a system HTTP_PROXY doesn't intercept localhost
loopbacks.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import httpx

from infra.contracts import DocumentChunkDTO, ProfileDTO, UserProfileDTO
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
