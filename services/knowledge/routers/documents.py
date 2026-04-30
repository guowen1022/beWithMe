import io
import re
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from infra.db import get_db, async_session
from silicon_brain.models.document import Document, DocumentChunk
from silicon_brain.schemas import DocumentCreate, DocumentRead
from infra.rag.embedding import embed_batch
from infra.auth import parse_user_id as get_current_user_id
from infra.topology import upstream_url


class UrlIngestRequest(BaseModel):
    url: HttpUrl


class FromExtractedRequest(BaseModel):
    """Internal — called by the browser sidecar after it extracts a page."""
    title: str
    text: str
    filename: Optional[str] = None
    url: Optional[str] = None


router = APIRouter()


def chunk_text(text: str, target_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text on paragraph boundaries, targeting ~target_size words per chunk."""
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        words = len(para.split())
        if current_len + words > target_size and current:
            chunks.append("\n\n".join(current))
            if words < overlap:
                current = [current[-1], para] if current else [para]
                current_len = len(current[-1].split()) + words
            else:
                current = [para]
                current_len = words
        else:
            current.append(para)
            current_len += words

    if current:
        chunks.append("\n\n".join(current))
    return chunks if chunks else [text]


async def _embed_document_chunks(document_id):
    """Background task to embed all chunks of a document."""
    async with async_session() as db:
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        chunks = result.scalars().all()
        if not chunks:
            return

        texts = [c.text for c in chunks]
        try:
            embeddings = await embed_batch(texts)
            for chunk, emb in zip(chunks, embeddings):
                chunk.embedding = emb
            await db.commit()
        except Exception as e:
            print(f"Failed to embed document chunks: {e}")


async def _persist_extracted_document(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    text: str,
    filename: Optional[str],
    background_tasks: BackgroundTasks,
) -> dict:
    """Shared write path — used by /from-extracted, /url, and /upload (PDFs)."""
    doc = Document(
        user_id=user_id,
        title=title,
        filename=filename,
        content=text,
        pdf_data=None,
    )
    db.add(doc)
    await db.flush()

    texts = chunk_text(text)
    for i, chunk_str in enumerate(texts):
        db.add(DocumentChunk(document_id=doc.id, chunk_index=i, text=chunk_str))

    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(_embed_document_chunks, doc.id)

    return {
        "id": str(doc.id),
        "title": doc.title,
        "filename": doc.filename,
        "text": text,
        "pages": 0,
    }


@router.post("/documents", response_model=DocumentRead)
async def create_document(
    body: DocumentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    doc = Document(user_id=user_id, title=body.title, filename=body.filename, content=body.content)
    db.add(doc)
    await db.flush()

    texts = chunk_text(body.content)
    for i, text in enumerate(texts):
        chunk = DocumentChunk(document_id=doc.id, chunk_index=i, text=text)
        db.add(chunk)

    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(_embed_document_chunks, doc.id)
    return DocumentRead.model_validate(doc)


@router.post("/documents/upload")
async def upload_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Upload a PDF file. Extracts text, stores the raw PDF bytes, chunks, and
    triggers background embedding. Returns the document ID + extracted text."""
    form = await request.form(max_part_size=50 * 1024 * 1024)
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="No file uploaded")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF too large (max 50 MB)")

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n\n".join(pages_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")

    if not full_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract any text from this PDF")

    title = file.filename.rsplit(".", 1)[0] if file.filename else "Untitled"

    doc = Document(
        user_id=user_id,
        title=title,
        filename=file.filename,
        content=full_text,
        pdf_data=pdf_bytes,
    )
    db.add(doc)
    await db.flush()

    texts = chunk_text(full_text)
    for i, text in enumerate(texts):
        chunk = DocumentChunk(document_id=doc.id, chunk_index=i, text=text)
        db.add(chunk)

    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(_embed_document_chunks, doc.id)

    return {
        "id": str(doc.id),
        "title": doc.title,
        "filename": doc.filename,
        "text": full_text,
        "pages": len(pages_text),
    }


@router.post("/documents/url")
async def ingest_url(
    body: UrlIngestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Render a URL via the browser sidecar, persist the extracted content."""
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        try:
            resp = await client.post(
                f"{upstream_url('browser')}/api/browser/render",
                json={"url": str(body.url)},
            )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=503, detail=f"browser sidecar unreachable: {e}")

    if resp.status_code != 200:
        # Pass through the browser's error verbatim so the user sees the same
        # message they'd see today (e.g. "captcha wall", "timed out").
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    payload = resp.json()
    return await _persist_extracted_document(
        db=db,
        user_id=user_id,
        title=payload.get("title") or str(body.url),
        text=payload["text"],
        filename=None,
        background_tasks=background_tasks,
    )


@router.post("/documents/from-extracted")
async def from_extracted(
    body: FromExtractedRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Internal — called by the browser sidecar's /resume after it extracts text."""
    return await _persist_extracted_document(
        db=db,
        user_id=user_id,
        title=body.title,
        text=body.text,
        filename=body.filename,
        background_tasks=background_tasks,
    )


@router.get("/documents/{document_id}/pdf")
async def get_document_pdf(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Serve the raw PDF bytes for in-browser display."""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or not doc.pdf_data:
        raise HTTPException(status_code=404, detail="PDF not found")
    return Response(
        content=doc.pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={doc.filename or 'document.pdf'}"},
    )


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return [DocumentRead.model_validate(d) for d in result.scalars().all()]
