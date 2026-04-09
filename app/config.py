from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://weng@localhost/bewithme"
    ollama_url: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    llm_model: str = "MiniMax-M2.7-highspeed"

    class Config:
        env_file = ".env"


settings = Settings()
