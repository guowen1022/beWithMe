"""Notes search — vector retrieval over the teacher's authored notes.

A note lives on disk inside the persona sidecar (per-user, per-block_id
.md files); the persona POSTs chunks here whenever a note is mounted,
re-pushed, or edited. We embed with nomic's `search_document:` prefix at
index time and `search_query:` prefix at query time — both prefixes are
applied here, never on the caller side, so the two halves of the
asymmetric pair stay in sync.

Replace-all-for-this-note semantics: each POST wipes existing rows for
`(user_id, note_id)` and inserts the new chunks. That keeps the live
.md the source of truth and avoids a stale-row reconciliation step in
the persona's editor.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.db import async_session, get_db
from infra.rag.embedding import embed_batch, embed_text
from silicon_brain.models.note_chunk import NoteChunk


router = APIRouter()


class _ChunkIn(BaseModel):
    block_start: int = Field(..., ge=0)
    block_end: int = Field(..., ge=0)
    text: str


class _UpsertRequest(BaseModel):
    note_id: str = Field(..., min_length=1)
    chunks: List[_ChunkIn]


class _NoteHitOut(BaseModel):
    note_id: str
    block_start: int
    block_end: int
    text: str
    score: float


async def _embed_note_chunks(user_id: UUID, note_id: str) -> None:
    """Background task: load freshly-inserted rows, embed the text with
    nomic's `search_document:` prefix, persist."""
    async with async_session() as db:
        result = await db.execute(
            text(
                "SELECT id, text FROM note_chunks "
                "WHERE user_id = :uid AND note_id = :nid AND embedding IS NULL"
            ),
            {"uid": str(user_id), "nid": note_id},
        )
        rows = result.fetchall()
        if not rows:
            return
        try:
            embeddings = await embed_batch(
                [r[1] for r in rows], task="search_document",
            )
        except Exception as e:
            print(f"[notes] embed failed for ({user_id}, {note_id}): {e}", flush=True)
            return
        for (chunk_id, _), emb in zip(rows, embeddings):
            await db.execute(
                text("UPDATE note_chunks SET embedding = :emb WHERE id = :id"),
                {"emb": str(list(emb)), "id": str(chunk_id)},
            )
        await db.commit()


@router.post("/notes/chunks")
async def upsert_note_chunks(
    body: _UpsertRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
) -> dict:
    """Replace all chunks for `(user_id, note_id)` and queue background
    embedding. Returns `{count}` of inserted chunks. Empty `chunks` is
    valid — it deletes any existing rows for the note (used when a note
    is forgotten)."""
    await db.execute(
        delete(NoteChunk).where(
            NoteChunk.user_id == user_id, NoteChunk.note_id == body.note_id,
        )
    )
    for c in body.chunks:
        db.add(NoteChunk(
            user_id=user_id,
            note_id=body.note_id,
            block_start=c.block_start,
            block_end=c.block_end,
            text=c.text,
        ))
    await db.commit()

    if body.chunks:
        background_tasks.add_task(_embed_note_chunks, user_id, body.note_id)
    return {"count": len(body.chunks)}


@router.get("/notes/search", response_model=List[_NoteHitOut])
async def search_notes(
    q: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
) -> List[_NoteHitOut]:
    """Find note chunks semantically related to `q`. Returns up to `top_k`
    chunks across all of this user's notes, sorted by cosine distance."""
    qvec = await embed_text(q, task="search_query")
    result = await db.execute(
        text(
            """
            SELECT note_id, block_start, block_end, text,
                   (embedding <=> :qvec) AS distance
              FROM note_chunks
             WHERE user_id = :uid AND embedding IS NOT NULL
             ORDER BY embedding <=> :qvec
             LIMIT :k
            """
        ),
        {"uid": str(user_id), "qvec": str(qvec), "k": top_k},
    )
    rows = result.fetchall()
    return [
        _NoteHitOut(
            note_id=r[0],
            block_start=r[1],
            block_end=r[2],
            text=r[3],
            score=float(1.0 - r[4]),  # cosine sim ≈ 1 - distance, for display
        )
        for r in rows
    ]
