from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Populate os.environ from .env so HTTP clients (httpx, openai SDK, anthropic
# SDK) see HTTPS_PROXY / NO_PROXY etc. pydantic-settings alone reads .env into
# Settings but does not propagate to os.environ.
load_dotenv()


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://weng@localhost/bewithme"
    ollama_url: str = "http://localhost:11434"

    # Sidecar topology. The shell binds to base_port, sidecars to base_port + offset
    # (see services.shell.proxy.SERVICE_OFFSETS). Per-service URL overrides like
    # ASK_SERVICE_URL still win when set, for cross-host deployments.
    base_port: int = 8000
    service_host: str = "localhost"
    # Active LLM provider — picks which backend in app/infra/model/ serves
    # all generate/stream calls. Override via LLM_PROVIDER env var.
    llm_provider: str = "deepseek"  # "minimax" | "deepseek"

    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # Provider credentials/URLs/models live in .env — no defaults.
    # Only the .env entries for the *active* provider need to be set;
    # the facade in app/infra/model/llm.py validates this at import time.

    # MiniMax (via Anthropic-compatible endpoint)
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    llm_model: str = ""

    # DeepSeek raw API — OpenAI-compatible
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    deepseek_model: str = ""

    # Local Whisper (pywhispercpp). Reuses Superwhisper's model by default.
    whisper_model_path: str = (
        "/Users/weng/Library/Application Support/Superwhisper/ggml-small.bin"
    )
    whisper_threads: int = 4

    # Local TTS (kokoro-onnx). Models live under beWithMe's own app-support dir.
    kokoro_model_path: str = (
        "/Users/weng/Library/Application Support/beWithMe/models/kokoro/kokoro-v1.0.onnx"
    )
    kokoro_voices_path: str = (
        "/Users/weng/Library/Application Support/beWithMe/models/kokoro/voices-v1.0.bin"
    )
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    kokoro_lang: str = "en-us"

    # Allow .env to carry vars that aren't declared fields here (e.g. NO_PROXY,
    # HTTPS_PROXY) — they get loaded into os.environ via load_dotenv() above
    # for httpx / openai SDK / anthropic SDK to consume directly.
    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
