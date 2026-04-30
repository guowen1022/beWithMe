"""Stateless infra config — read from .env, used by infra.* and consumers.

Owns the foundation deps everyone needs: Ollama (embedding host) and the LLM
provider. No DB knowledge, no domain. Each top-level package has its own
Settings; they coexist over a single .env via extra="ignore".
"""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
