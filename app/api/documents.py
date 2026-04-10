import re
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session
from app.models.document import Document, DocumentChunk
from app.schemas.query import DocumentCreate, DocumentRead
from app.services.embedding import embed_batch
from app.api.deps import get_current_user_id

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
            # Keep last paragraph for overlap
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


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return [DocumentRead.model_validate(d) for d in result.scalars().all()]
