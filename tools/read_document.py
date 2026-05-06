"""read_document — teacher's tool for actively reading PDF content.

Three actions, mutually exclusive:
  - "outline": fetch table of contents + page count for the document
  - "page":    fetch the full text of one page (1-indexed)
  - "query":   embed `query` and vector-search for top-k chunks (each chunk
               carries its `page_number`)

`document_id` is optional. When omitted, we resolve it from the user's canvas
state — find the mounted block whose `state.kind == "pdf"` and use its
`extra["document_id"]`. If 0 or 2+ PDFs are mounted, return an error so the
teacher can disambiguate explicitly.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from infra.rag.embedding import embed_text
from persona.teacher.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools.read_media import read_media


async def _resolve_document_id_from_canvas(user_id: UUID) -> tuple[Optional[UUID], Optional[str]]:
    """Find the document_id of the single mounted PDF on the user's canvas.

    Returns `(document_id, error_message)`. `document_id` is set when exactly
    one PDF reader is mounted with a real document_id; otherwise the error
    message tells the teacher what to do.
    """
    try:
        perc = await read_media(user_id)
    except Exception as e:
        return None, f"could not read canvas state: {e}"

    pdf_doc_ids: list[UUID] = []
    for canvas in perc.canvases:
        if not canvas.online:
            continue
        for block in canvas.blocks:
            state = block.state
            if state is None or state.kind != "pdf":
                continue
            extra = state.extra or {}
            doc_id_raw = extra.get("document_id")
            if not doc_id_raw:
                continue
            try:
                pdf_doc_ids.append(UUID(str(doc_id_raw)))
            except ValueError:
                continue

    # De-dupe across devices showing the same PDF.
    unique = list({str(d): d for d in pdf_doc_ids}.values())

    if len(unique) == 1:
        return unique[0], None
    if not unique:
        return None, "no PDF is currently on canvas — pass document_id explicitly"
    return None, (
        f"{len(unique)} PDFs are on canvas; pass document_id explicitly "
        "(read_media returns the document_id of each)"
    )


async def read_document(
    *,
    user_id: UUID,
    action: str,
    document_id: Optional[UUID] = None,
    page: Optional[int] = None,
    query: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Dispatch to the requested action. Returns a JSON-serialisable dict.

    Errors are returned as `{"error": "..."}` rather than raised so the
    tool executor can hand them straight back to the LLM.
    """
    action = (action or "").strip().lower()
    if action not in ("outline", "page", "query"):
        return {"error": "action must be 'outline', 'page', or 'query'"}

    if document_id is None:
        resolved, err = await _resolve_document_id_from_canvas(user_id)
        if err:
            return {"error": err}
        document_id = resolved

    client = SiliconBrainClient()
    try:
        if action == "outline":
            return await client.get_document_structure(user_id, document_id)

        if action == "page":
            if page is None:
                return {"error": "page is required when action='page'"}
            try:
                page_int = int(page)
            except (TypeError, ValueError):
                return {"error": "page must be an integer"}
            if page_int < 1:
                return {"error": "page must be >= 1"}
            try:
                return await client.get_document_page(user_id, document_id, page_int)
            except Exception as e:
                # silicon_brain returns 404 for out-of-range; surface the
                # message verbatim so the teacher knows.
                return {"error": f"failed to fetch page {page_int}: {e}"}

        # action == "query"
        if not (query and query.strip()):
            return {"error": "query is required when action='query'"}
        try:
            embedding = await embed_text(query)
        except Exception as e:
            return {"error": f"failed to embed query: {e}"}
        try:
            chunks = await client.search_document_chunks(
                user_id, document_id, embedding, top_k=top_k,
            )
        except Exception as e:
            return {"error": f"vector search failed: {e}"}
        return {
            "document_id": str(document_id),
            "query": query,
            "chunks": [
                {
                    "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                }
                for c in chunks
            ],
        }
    finally:
        await client.aclose()


__all__ = ["read_document"]
