from typing import Optional, List
import httpx
from app.config import settings

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.ollama_url, timeout=30.0)
    return _client


async def embed_text(text: str) -> List[float]:
    client = _get_client()
    resp = await client.post("/api/embed", json={"model": settings.embedding_model, "input": text})
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


async def embed_batch(texts: List[str]) -> List[List[float]]:
    client = _get_client()
    resp = await client.post("/api/embed", json={"model": settings.embedding_model, "input": texts})
    resp.raise_for_status()
    return resp.json()["embeddings"]
