"""The user-data map — one place that knows where every user's data lives.

Each domain owns its own persistence (a slice keyed by ``user_id``), exactly as
``ARCHITECTURE.md`` allows. This module does not change that. It only provides
the *index*: given a ``user_id``, enumerate every store that holds that person's
data so we can (a) report where it lives and (b) erase it in one shot.

The whole point is drift-resistance — adding new user data later must land in
this map *without* anyone remembering to touch a central list:

  * Database tables — captured **automatically**. Every domain declares its
    tables on the shared ``infra.db.Base``; any table with a ``user_id`` column
    is user-scoped by construction, so we discover them from
    ``Base.metadata`` at runtime. A new domain on the shared Base is covered
    for free.

  * Disk directories — captured by **registration**. Filesystem roots like
    ``data/sessions/<user_id>/`` are plain string constants scattered across
    modules; there is nothing to introspect. So each owner calls
    :func:`register_user_dir` next to its path constant, and the guard test
    ``tests/unit/test_user_data_map.py`` fails CI if a ``data/<x>/<user_id>/``
    store ever appears in the source without being registered.

This module lives in ``infra`` (the leaf): it imports nothing from
silicon_brain / persona / services, so every layer can ``register_user_dir``
into it without inverting the dep graph. It is *mechanism only* — it does not
decide who triggers a purge or expose any endpoint. Today the entry point is
``scripts/purge_user.py``; a user-initiated flow can wrap the same functions
later.

Purge deletes **per table, keyed by user_id** rather than relying on
``ON DELETE CASCADE`` from the ``users`` row. That is deliberate: not every
user-scoped table wires the cascade (e.g. ``note_chunks`` has a bare ``user_id``
with no foreign key), so a cascade-only wipe would silently orphan rows.
Per-table deletion is correct regardless of whether the FK exists.
"""
from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select

from infra.db import Base

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Disk registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserDataDir:
    """A disk root whose immediate children are ``<user_id>/`` directories."""

    domain: str          # owning area, e.g. "teacher", "canvas"
    name: str            # the data/<name> leaf, e.g. "sessions"
    root: Path           # absolute path to data/<name>
    description: str     # what user data lives here


_DIRS: dict[Path, UserDataDir] = {}


def register_user_dir(domain: str, root: Path, description: str) -> Path:
    """Register a ``data/<name>/<user_id>/`` root as holding user data.

    Idempotent — call it at module import next to your path constant. Returns
    ``root`` so it can be used inline. Example::

        DATA_DIR = register_user_dir(
            "teacher", _repo_root() / "data" / "sessions",
            "Per-session transcripts and summaries.",
        )
    """
    root = Path(root).resolve()
    _DIRS[root] = UserDataDir(domain=domain, name=root.name, root=root, description=description)
    return root


def user_dir(root: Path, user_id: UUID) -> Path:
    """Return ``root/<user_id>/``, creating it. Preferred accessor for new code.

    ``root`` must already be registered via :func:`register_user_dir`.
    """
    root = Path(root).resolve()
    if root not in _DIRS:
        raise ValueError(f"{root} is not a registered user-data root; call register_user_dir() first")
    path = root / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def registered_dirs() -> list[UserDataDir]:
    return sorted(_DIRS.values(), key=lambda d: d.name)


# ---------------------------------------------------------------------------
# Domain loading — populate metadata + the disk registry
# ---------------------------------------------------------------------------

# Model packages: importing them registers every ORM table on Base.metadata.
_MODEL_MODULES: tuple[str, ...] = (
    "silicon_brain.models",
    "persona.teacher.models",
    "persona.teacher.knowledge.models",
)

# Modules that own a user-scoped disk root and call register_user_dir() at import.
_DISK_MODULES: tuple[str, ...] = (
    "persona.teacher.session.transcriber",   # data/sessions
    "workshop.canvas.tools._note_cache",     # data/notes
    "workshop.research.recipe_store",        # data/research
    "services.knowledge.routers.media",      # data/uploads
    "agents.frontend_engineer.workspace",    # data/canvases
)


def load_domains() -> None:
    """Import every domain that owns user data, so the map is complete.

    Mirrors the import-then-introspect pattern in ``scripts/init_db.py``. Uses
    ``importlib`` (string names, not static imports) so ``infra`` keeps its
    leaf position in the dep graph. Idempotent — Python caches modules.
    """
    for mod in (*_MODEL_MODULES, *_DISK_MODULES):
        importlib.import_module(mod)


# ---------------------------------------------------------------------------
# Database tables
# ---------------------------------------------------------------------------

_ANCHOR_TABLE = "users"  # the User row itself — keyed by `id`, deleted last.


def user_scoped_tables() -> list["Table"]:
    """Every table that directly holds rows for one user, in deletion order.

    A table is user-scoped if it has a ``user_id`` column. The ``users`` anchor
    (keyed by ``id``) is included last. Order is child-before-parent so
    intra-set foreign keys (e.g. ``concept_edges`` → ``concept_nodes``) don't
    block deletion. Call :func:`load_domains` first so metadata is populated.

    Tables without ``user_id`` that hang off a user-scoped parent via
    ``ON DELETE CASCADE`` (e.g. ``document_chunks`` → ``documents``) are *not*
    listed — they vanish when their parent row is deleted.
    """
    md = Base.metadata
    targets = {
        t.name for t in md.tables.values()
        if "user_id" in t.c or t.name == _ANCHOR_TABLE
    }
    # reversed topological order = children first, parents (incl. users) last.
    return [t for t in reversed(md.sorted_tables) if t.name in targets]


def _user_filter(table: "Table", user_id: UUID):
    col = table.c.id if table.name == _ANCHOR_TABLE else table.c.user_id
    return col == user_id


# ---------------------------------------------------------------------------
# Read: where is this user's data?
# ---------------------------------------------------------------------------

async def describe_user_data(session: "AsyncSession", user_id: UUID) -> dict:
    """Build the data map for one user: per-table row counts + per-dir presence.

    Read-only. Returns a JSON-able dict; counts of 0 / missing dirs are kept so
    the map is a complete inventory, not just the non-empty parts.
    """
    tables = []
    for table in reversed(user_scoped_tables()):  # report parent-first for readability
        count = await session.scalar(
            select(func.count()).select_from(table).where(_user_filter(table, user_id))
        )
        tables.append({"table": table.name, "rows": int(count or 0)})

    dirs = []
    for d in registered_dirs():
        path = d.root / str(user_id)
        exists = path.exists()
        n_files = total_bytes = 0
        if exists:
            for p in path.rglob("*"):
                if p.is_file():
                    n_files += 1
                    total_bytes += p.stat().st_size
        dirs.append({
            "domain": d.domain,
            "name": d.name,
            "path": str(path),
            "exists": exists,
            "files": n_files,
            "bytes": total_bytes,
            "description": d.description,
        })

    return {
        "user_id": str(user_id),
        "tables": tables,
        "dirs": dirs,
        "total_rows": sum(t["rows"] for t in tables),
        "total_files": sum(d["files"] for d in dirs),
        "total_bytes": sum(d["bytes"] for d in dirs),
    }


# ---------------------------------------------------------------------------
# Write: erase this user's data, one shot
# ---------------------------------------------------------------------------

async def purge_user_data(session: "AsyncSession", user_id: UUID) -> dict:
    """Delete every trace of one user — DB rows then disk dirs — in one call.

    DB deletes run in the caller's transaction (the caller commits). Disk
    removal happens after; a returned ``errors`` list captures any dir that
    could not be removed so the caller can surface it rather than failing silently.
    """
    deleted_rows: dict[str, int] = {}
    for table in user_scoped_tables():  # child-first, users last
        result = await session.execute(delete(table).where(_user_filter(table, user_id)))
        deleted_rows[table.name] = result.rowcount or 0

    removed_dirs: list[str] = []
    errors: list[str] = []
    for d in registered_dirs():
        path = d.root / str(user_id)
        if path.exists():
            try:
                shutil.rmtree(path)
                removed_dirs.append(str(path))
            except OSError as exc:  # noqa: PERF203 — report, don't abort
                errors.append(f"{path}: {exc}")

    return {
        "user_id": str(user_id),
        "deleted_rows": deleted_rows,
        "total_rows": sum(deleted_rows.values()),
        "removed_dirs": removed_dirs,
        "errors": errors,
    }
