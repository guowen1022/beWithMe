"""Typed HTTP client for the silicon_brain (knowledge sidecar).

Persona's only runtime dependency on silicon_brain is via this client. Every
method maps to one knowledge endpoint and returns a DTO from `infra.contracts`.

Auth: every call auto-injects `X-User-Id`. Knowledge sidecars trust the header
because the shell already verified it (or, for inter-service calls inside the
trusted network, the caller asserts on behalf of an authenticated user).

Network: `trust_env=False` so a system HTTP_PROXY doesn't intercept localhost
loopbacks (we hit a stale-cache silent-bypass before — see CLAUDE.md
auth notes).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import httpx

from infra.contracts import (
    BrainStateDTO,
    ConceptDTO,
    DocumentChunkDTO,
    InteractionCreateDTO,
    InteractionDTO,
    RecommendationCreateDTO,
    RecommendationDTO,
    SessionSummaryUpsertDTO,
    SummaryDTO,
    UserProfileDTO,
)
from infra.topology import upstream_url


def _user_headers(user_id: UUID, extra: Optional[dict] = None) -> dict:
    h = {"X-User-Id": str(user_id)}
    if extra:
        h.update(extra)
    return h


class SiliconBrainClient:
    """Async HTTP client for the knowledge sidecar.

    One instance per process; share via FastAPI app.state. Owns its own
    httpx.AsyncClient so connection pooling works.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self._base = (base_url or upstream_url("knowledge")).rstrip("/")
        # trust_env=False — see module docstring.
        self._http = httpx.AsyncClient(base_url=self._base, timeout=timeout, trust_env=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Reads ---

    async def get_brain_state(
        self,
        user_id: UUID,
        *,
        session_id: Optional[UUID] = None,
        concept_limit: int = 30,
    ) -> BrainStateDTO:
        params: dict = {"concept_limit": concept_limit}
        if session_id is not None:
            params["session_id"] = str(session_id)
        resp = await self._http.get("/api/brain-state", headers=_user_headers(user_id), params=params)
        resp.raise_for_status()
        return BrainStateDTO.model_validate(resp.json())

    async def get_user_profile_state(
        self,
        user_id: UUID,
        *,
        session_id: Optional[UUID] = None,
    ) -> UserProfileDTO:
        params: dict = {}
        if session_id is not None:
            params["session_id"] = str(session_id)
        resp = await self._http.get("/api/user-profile-state", headers=_user_headers(user_id), params=params)
        resp.raise_for_status()
        return UserProfileDTO.model_validate(resp.json())

    async def get_concepts(self, user_id: UUID, *, limit: int = 30) -> list[ConceptDTO]:
        resp = await self._http.get(
            "/api/concepts-list",
            headers=_user_headers(user_id),
            params={"limit": limit},
        )
        resp.raise_for_status()
        return [ConceptDTO.model_validate(x) for x in resp.json()]

    async def get_session_history(self, user_id: UUID, session_id: UUID) -> list[InteractionDTO]:
        resp = await self._http.get(
            f"/api/sessions/{session_id}/interactions",
            headers=_user_headers(user_id),
        )
        resp.raise_for_status()
        return [InteractionDTO.model_validate(x) for x in resp.json()]

    async def get_graph_context(self, user_id: UUID, concept_names: list[str]) -> str:
        if not concept_names:
            return ""
        resp = await self._http.get(
            "/api/graph-context",
            headers=_user_headers(user_id),
            params=[("concepts", n) for n in concept_names],
        )
        resp.raise_for_status()
        return resp.json().get("context", "")

    async def boost_embedding(self, user_id: UUID, query_embedding: list[float]) -> list[float]:
        resp = await self._http.post(
            "/api/preferences/boost-embedding",
            headers=_user_headers(user_id),
            json={"query_embedding": query_embedding},
        )
        resp.raise_for_status()
        return resp.json()["boosted"]

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

    async def search_past_summaries(
        self,
        user_id: UUID,
        query_embedding: list[float],
        *,
        top_k: int = 3,
    ) -> list[SummaryDTO]:
        resp = await self._http.post(
            "/api/retrieval/past-summaries",
            headers=_user_headers(user_id),
            json={"query_embedding": query_embedding, "top_k": top_k},
        )
        resp.raise_for_status()
        return [SummaryDTO.model_validate(x) for x in resp.json()]

    async def list_recommendations(
        self,
        user_id: UUID,
        *,
        source: Optional[str] = None,
        category: Optional[str] = None,
        status: str = "active",
    ) -> list[RecommendationDTO]:
        params: dict = {"status": status}
        if source:
            params["source"] = source
        if category:
            params["category"] = category
        resp = await self._http.get(
            "/api/recommendations",
            headers=_user_headers(user_id),
            params=params,
        )
        resp.raise_for_status()
        return [RecommendationDTO.model_validate(x) for x in resp.json()]

    # --- Writes ---

    async def replace_active_recommendations(
        self,
        user_id: UUID,
        source: str,
        recs: list[RecommendationCreateDTO],
    ) -> list[RecommendationDTO]:
        resp = await self._http.post(
            "/api/recommendations/replace-active",
            headers=_user_headers(user_id),
            json={
                "source": source,
                "recommendations": [r.model_dump(mode="json") for r in recs],
            },
        )
        resp.raise_for_status()
        return [RecommendationDTO.model_validate(x) for x in resp.json()]

    async def update_recommendation_status(
        self,
        user_id: UUID,
        rec_id: UUID,
        status: str,
    ) -> RecommendationDTO:
        resp = await self._http.patch(
            f"/api/recommendations/{rec_id}/status",
            headers=_user_headers(user_id),
            json={"status": status},
        )
        resp.raise_for_status()
        return RecommendationDTO.model_validate(resp.json())

    async def upsert_session_summary(
        self,
        user_id: UUID,
        summary: SessionSummaryUpsertDTO,
    ) -> SummaryDTO:
        resp = await self._http.post(
            "/api/sessions/summaries",
            headers=_user_headers(user_id),
            json=summary.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return SummaryDTO.model_validate(resp.json())

    async def create_interaction(
        self,
        user_id: UUID,
        body: InteractionCreateDTO,
    ) -> InteractionDTO:
        resp = await self._http.post(
            "/api/interactions",
            headers=_user_headers(user_id),
            json=body.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return InteractionDTO.model_validate(resp.json())

    async def fire_brain_builder_update(
        self,
        user_id: UUID,
        interaction_id: UUID,
    ) -> None:
        """Fire-and-forget. Knowledge schedules the brain-builder background task."""
        resp = await self._http.post(
            "/api/brain-builder/post-interaction",
            headers=_user_headers(user_id),
            json={"interaction_id": str(interaction_id)},
        )
        resp.raise_for_status()
