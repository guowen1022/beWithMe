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

    # skillforge — the external tuning framework (separate project). DEFAULT OFF:
    # empty url → the adapter (infra/skillforge_client.py) fails open and beWithMe
    # behaves exactly as today. Set to the edge service URL to enable tuning.
    skillforge_edge_url: str = ""
    skillforge_host: str = "beWithMe"
    # Store + eval services of the same skillforge instance. Consumed only by
    # the tuning sidecar's self-registration (services/tuning/registration.py);
    # empty → registration is skipped (fail-open), the eval endpoint still serves.
    skillforge_store_url: str = ""
    skillforge_eval_svc_url: str = ""

    # --- Authentication -------------------------------------------------
    # ARCHITECTURE.md section 6: the default trust model verifies only that a
    # user id EXISTS, so `X-User-Id` is an unverified assertion. That is fine
    # on one machine and unsafe the moment the shell has a public address.
    #
    # "legacy" reproduces that behaviour exactly (no UX change, nothing breaks).
    # "strict" requires a signed session token and stops trusting the header.
    # See docs/SECURITY.md.
    bewithme_auth_mode: str = "legacy"  # "legacy" | "strict"

    # HMAC key for infra/session_token.py. Required in strict mode; the shell
    # refuses to start without it. Generate with:
    #   python -c "from infra.session_token import generate_secret_key as g; print(g())"
    bewithme_secret_key: str = ""

    # Shared access key a client presents once to obtain a session token in
    # strict mode. Empty in strict mode => no token can ever be issued.
    bewithme_access_key: str = ""

    # Comma-separated CORS origins for the shell. Empty keeps the historical
    # localhost:3000/3002 pair, so local dev is unaffected; any real deployment
    # must set this to the frontend's actual origin.
    bewithme_cors_origins: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
