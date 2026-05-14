from typing import Literal, Optional, List
import httpx
from infra.config import settings

_client: Optional[httpx.AsyncClient] = None

# nomic-embed-text task prefixes. Setting a task is OPT-IN — existing
# callers (and the four pgvector tables they wrote) keep their unprefixed
# embeddings. New asymmetric pipelines (notes) should pass
# task="search_document" at index time and task="search_query" at query
# time; mixing the two corrupts retrieval, so always pair them.
EmbedTask = Literal["search_document", "search_query"]


def _apply_task(text: str, task: Optional[EmbedTask]) -> str:
    return f"{task}: {text}" if task else text


async def embed_text(text: str, *, task: Optional[EmbedTask] = None) -> List[float]:
    client = _get_client()
    payload = _apply_task(text, task)
    resp = await client.post("/api/embed", json={"model": settings.embedding_model, "input": payload})
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


async def embed_batch(texts: List[str], *, task: Optional[EmbedTask] = None) -> List[List[float]]:
    client = _get_client()
    payload = [_apply_task(t, task) for t in texts]
    resp = await client.post("/api/embed", json={"model": settings.embedding_model, "input": payload})
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.ollama_url, timeout=30.0)
    return _client
