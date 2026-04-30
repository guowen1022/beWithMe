import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    checks = {"db": False, "ollama": False}

    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            checks["ollama"] = resp.status_code == 200
    except Exception:
        pass

    ok = all(checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}
