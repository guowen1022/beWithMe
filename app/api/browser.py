"""Browser control endpoints — handoff for captcha-solving, resume for extraction."""
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.silicon_brain.models.document import Document, DocumentChunk
from app.api.deps import get_current_user_id
from app.api.documents import chunk_text, _embed_document_chunks
import trafilatura

router = APIRouter()


class HandoffRequest(BaseModel):
    url: HttpUrl


def _active_page(request: Request):
    """Return the page the user is currently viewing (browse or handoff)."""
    for attr in ("browse_page", "handoff_page"):
        page = getattr(request.app.state, attr, None)
        if page and not page.is_closed():
            return page
    return None


@router.get("/browser/status")
async def browser_status(request: Request):
    context = getattr(request.app.state, "browser_context", None)
    headed = getattr(request.app.state, "browser_headed", False)
    if context is None:
        return {"status": "not_running", "headed": False}
    return {
        "status": "running",
        "headed": headed,
        "pages": len(context.pages),
        "urls": [p.url for p in context.pages],
    }


@router.get("/browser/selection")
async def browser_selection(request: Request):
    """Return the text the user currently has selected in the browser page."""
    page = _active_page(request)
    if not page:
        return {"selection": "", "url": ""}
    try:
        sel = await page.evaluate("window.getSelection().toString()")
        return {"selection": (sel or "").strip(), "url": page.url}
    except Exception:
        return {"selection": "", "url": ""}


@router.post("/browser/handoff")
async def browser_handoff(body: HandoffRequest, request: Request):
    """Open a URL in the visible browser window for manual interaction (captcha,
    login, cookie consent). Call /browser/resume when done to extract content."""
    context = getattr(request.app.state, "browser_context", None)
    headed = getattr(request.app.state, "browser_headed", False)
    if context is None:
        raise HTTPException(status_code=503, detail="Browser not ready")
    if not headed:
        raise HTTPException(
            status_code=400,
            detail="Browser is headless. Restart with BROWSER_HEADED=1 to enable handoff.",
        )

    old_page = getattr(request.app.state, "handoff_page", None)
    if old_page and not old_page.is_closed():
        await old_page.close()

    page = await context.new_page()
    try:
        await page.goto(str(body.url), wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        await page.close()
        raise HTTPException(status_code=400, detail=f"Failed to load URL: {e}")
    await page.bring_to_front()
    request.app.state.handoff_page = page
    return {
        "status": "ok",
        "message": "Browser opened. Solve the captcha or log in, then click Resume.",
    }


@router.post("/browser/resume")
async def browser_resume(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Extract text from the handoff page after the user has interacted with it
    (solved captcha, logged in, etc.). Creates a document and triggers embedding."""
    page = getattr(request.app.state, "handoff_page", None)
    if page is None or page.is_closed():
        raise HTTPException(status_code=400, detail="No active handoff. Call /browser/handoff first.")

    try:
        html = await page.content()
        title = ((await page.title()) or page.url).strip()

        text = trafilatura.extract(
            html, include_comments=False, include_tables=True, output_format="markdown",
        )
        if not text or not text.strip():
            try:
                text = await page.inner_text("body")
                if text:
                    text = "\n\n".join(line for line in text.splitlines() if line.strip())
            except Exception:
                text = None

        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract readable text from this page.")

        text = text.strip()

        doc = Document(user_id=user_id, title=title, filename=None, content=text, pdf_data=None)
        db.add(doc)
        await db.flush()

        texts = chunk_text(text)
        for i, chunk_str in enumerate(texts):
            db.add(DocumentChunk(document_id=doc.id, chunk_index=i, text=chunk_str))

        await db.commit()
        await db.refresh(doc)

        background_tasks.add_task(_embed_document_chunks, doc.id)

        return {"id": str(doc.id), "title": doc.title, "filename": None, "text": text, "pages": 0}
    finally:
        await page.close()
        request.app.state.handoff_page = None
