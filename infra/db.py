"""Persistence machinery — the shared Base + engine + session for the whole project.

Lives in infra (the leaf) so every domain can build on it without violating
the one-direction dep graph:

  * silicon_brain/models/* — user-scoped tables (User, Profile, Interaction, …)
  * persona/<name>/models/* — persona-private tables (e.g., teacher's
    self-memory). None today.
  * services/<name>/models/* — service-private tables (cache, audit, quota).
    None today.

Each declares classes on `Base`. SQLAlchemy `create_all` sees every model
that's been imported by the time it's called.

Exports:
  - Base       — SQLAlchemy `DeclarativeBase`. The single ORM root.
  - engine     — single `AsyncEngine` bound to `infra.config.settings.database_url`.
  - async_session — `async_sessionmaker` for opening sessions from background tasks.
  - get_db     — async generator for FastAPI `Depends(get_db)`. Plain async def,
                 no FastAPI imports — the framework just sees a callable.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infra.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI-shaped dependency. `Depends(get_db)` yields an `AsyncSession`."""
    async with async_session() as session:
        yield session
