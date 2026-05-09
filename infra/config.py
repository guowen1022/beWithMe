"""Stateless infra config — read from .env, used by infra.* and consumers.

Owns the foundation knobs everyone shares:
  * the persistence URL (every domain inherits Base from infra.db)
  * Ollama (embedding host)
  * the LLM provider

Domain-specific config lives with the domain. Sidecar-local config (Whisper /
Kokoro paths) lives with that sidecar.
"""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    # Persistence — the single Postgres URL the whole project uses.
    # Each domain (silicon_brain, persona/<name>, services/<name>) declares
    # its own ORM models on infra.db.Base; they all live in this one DB.
    database_url: str = "postgresql+asyncpg://weng@localhost/bewithme"

    # Embedding (Ollama-hosted)
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # LLM provider — picks which backend in infra/model/ serves all
    # generate/stream calls.
    llm_provider: str = "deepseek"  # "minimax" | "deepseek"

    # MiniMax (via Anthropic-compatible endpoint)
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    llm_model: str = ""

    # DeepSeek raw API — OpenAI-compatible
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    deepseek_model: str = ""

    # Vision (image-understanding) provider — separate from llm_provider.
    # Selects the backend in infra/model/vision/. The main reasoning LLM
    # (llm_provider) stays text-only; vision calls are delegated here.
    vision_provider: str = "doubao"

    # Doubao Seed 2.0 Lite via Volces Ark (OpenAI-compatible)
    doubao_api_key: str = ""
    doubao_base_url: str = ""
    doubao_vision_model: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
