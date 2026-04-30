"""silicon_brain config — owns the database URL only.

Everything else stateless lives in infra.config.
"""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://weng@localhost/bewithme"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
