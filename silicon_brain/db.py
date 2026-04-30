"""silicon_brain owns the persistence layer end-to-end.

Exports:
  - Base       — SQLAlchemy DeclarativeBase. Every ORM model in the project
                 inherits from this (silicon_brain models, app/teacher models).
  - engine     — single AsyncEngine bound to settings.database_url.
  - async_session — sessionmaker for opening sessions from background tasks.
  - get_db     — async generator for FastAPI Depends(); plain Python, no
                 FastAPI imports.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from silicon_brain.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI-shaped dependency. `Depends(get_db)` yields an AsyncSession."""
    async with async_session() as session:
        yield session
