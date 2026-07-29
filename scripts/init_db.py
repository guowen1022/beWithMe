"""Create the bewithme database and apply the schema.

Schema is sourced from the SQLAlchemy models — every model declared on
`infra.db.Base` becomes a table here. To add a new table, just write the
model class; this script picks it up automatically on the next run.

Run anytime — `create_all` is idempotent (won't recreate existing tables).
The legacy `MIGRATE` block at the bottom handles ALTERs for dev DBs that
predate today's schema (e.g., adding `user_id` columns to old tables).
"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Make the project root importable when invoked as `python scripts/init_db.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

# `infra.config` calls load_dotenv() at import, so a local `.env` populates
# os.environ before we read DATABASE_URL below. In containers the value is
# already in the environment and this just no-ops. Guarded because init_db
# must stay runnable on a box that has no LLM provider vars configured yet.
try:
    import infra.config  # noqa: F401
except Exception:
    pass


# Connection target — `postgres` (admin DB) for CREATE DATABASE,
# then `bewithme` for the schema.
#
# Derived from DATABASE_URL when set (containers, CI) so this script targets the
# same server as the app rather than a developer's local socket. `_create_all_models`
# below already goes through `infra.db.engine`, which reads DATABASE_URL — without
# this, the two halves of the script would talk to different databases.
DB_NAME = "bewithme"

_raw_url = os.environ.get("DATABASE_URL", "")
if _raw_url:
    # asyncpg wants a plain libpq URL; SQLAlchemy's `+asyncpg` scheme is not one.
    APP_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    _parsed = urlparse(APP_URL)
    DB_NAME = _parsed.path.lstrip("/") or DB_NAME
    # CREATE DATABASE can't run from inside the database being created, so the
    # admin connection targets the always-present `postgres` maintenance DB.
    ADMIN_URL = urlunparse(_parsed._replace(path="/postgres"))
else:
    ADMIN_URL = "postgresql://weng@localhost/postgres"
    APP_URL = f"postgresql://weng@localhost/{DB_NAME}"


# Extensions must exist before SQLAlchemy create_all can create vector columns.
EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
"""


# Legacy ALTERs for dev DBs that predate the current schema. Each is
# idempotent (IF NOT EXISTS / DO $$ BEGIN ... IF NOT EXISTS). Safe to re-run.
MIGRATE = """
-- Add labels array to existing session_summaries tables
ALTER TABLE IF EXISTS session_summaries ADD COLUMN IF NOT EXISTS labels TEXT[];
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='session_summaries' AND column_name='domain') THEN
        UPDATE session_summaries SET labels = ARRAY[domain] WHERE domain IS NOT NULL AND labels IS NULL;
        ALTER TABLE session_summaries DROP COLUMN domain;
    END IF;
END $$;

-- HLR columns on existing concept_nodes
ALTER TABLE IF EXISTS concept_nodes ADD COLUMN IF NOT EXISTS half_life_hours DOUBLE PRECISION NOT NULL DEFAULT 24.0;
ALTER TABLE IF EXISTS concept_nodes ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ;

-- Voice prefs on existing user_preferences (P3).
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS voice_id TEXT NOT NULL DEFAULT 'af_heart';
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS voice_speed DOUBLE PRECISION NOT NULL DEFAULT 1.0;
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS voice_lang TEXT NOT NULL DEFAULT 'en-us';

-- Per-device talk channel (voice / text / both). Replaces the earlier
-- free-text talk_preference column; we drop that here so existing dev
-- DBs converge on the structured shape.
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS talk_desktop TEXT NOT NULL DEFAULT 'both';
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS talk_tablet TEXT NOT NULL DEFAULT 'both';
ALTER TABLE IF EXISTS user_preferences ADD COLUMN IF NOT EXISTS talk_phone TEXT NOT NULL DEFAULT 'text';
ALTER TABLE IF EXISTS user_preferences DROP COLUMN IF EXISTS talk_preference;

-- Recursive question on existing interactions
ALTER TABLE IF EXISTS interactions ADD COLUMN IF NOT EXISTS parent_interaction_id UUID REFERENCES interactions(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS interactions ADD COLUMN IF NOT EXISTS title VARCHAR(200);
CREATE INDEX IF NOT EXISTS idx_interactions_parent ON interactions(parent_interaction_id);

-- PDF structure: outline + page_count on documents, page_number on chunks.
ALTER TABLE IF EXISTS documents ADD COLUMN IF NOT EXISTS outline JSONB;
ALTER TABLE IF EXISTS documents ADD COLUMN IF NOT EXISTS page_count INTEGER;
ALTER TABLE IF EXISTS document_chunks ADD COLUMN IF NOT EXISTS page_number INTEGER;

-- Default user (used by some legacy data and tests).
-- created_at is set explicitly: the User model declares `default=datetime.utcnow`,
-- which SQLAlchemy applies Python-side on ORM inserts only. The generated DDL is
-- NOT NULL with no server default, so a raw INSERT like this one must supply it
-- or it trips a NotNullViolationError on every fresh database.
INSERT INTO users (id, username, created_at) VALUES ('00000000-0000-0000-0000-000000000000', 'default', NOW())
    ON CONFLICT (username) DO NOTHING;

-- Backfill user_id columns on legacy tables that pre-date multi-tenancy.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='profile')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='profile' AND column_name='user_id') THEN
        ALTER TABLE profile ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE profile SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE profile ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE profile ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_profile_user ON profile(user_id);
        ALTER TABLE profile ADD CONSTRAINT profile_user_id_key UNIQUE (user_id);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='interactions')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='interactions' AND column_name='user_id') THEN
        ALTER TABLE interactions ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE interactions SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE interactions ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE interactions ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='documents')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='user_id') THEN
        ALTER TABLE documents ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE documents SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE documents ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE documents ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='concept_nodes')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='concept_nodes' AND column_name='user_id') THEN
        ALTER TABLE concept_nodes ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE concept_nodes SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE concept_nodes ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE concept_nodes ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_concept_nodes_user ON concept_nodes(user_id);
        ALTER TABLE concept_nodes DROP CONSTRAINT IF EXISTS concept_nodes_name_key;
        DROP INDEX IF EXISTS concept_nodes_name_key;
        ALTER TABLE concept_nodes ADD CONSTRAINT concept_nodes_user_id_name_key UNIQUE (user_id, name);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='concept_edges')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='concept_edges' AND column_name='user_id') THEN
        ALTER TABLE concept_edges ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE concept_edges SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE concept_edges ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE concept_edges ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_concept_edges_user ON concept_edges(user_id);
    END IF;
END $$;

-- note_chunks.user_id predates the FK convention: it was a bare column with no
-- ON DELETE CASCADE, so deleting a users row would orphan its chunks. Add the
-- FK so it matches every other user-scoped table and the user-data purge map
-- (infra/user_data.py). create_all names this constraint identically on fresh
-- DBs, so the guard below is a no-op there.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='note_chunks')
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.table_constraints
           WHERE table_name='note_chunks' AND constraint_name='note_chunks_user_id_fkey'
       ) THEN
        DELETE FROM note_chunks WHERE user_id NOT IN (SELECT id FROM users);
        ALTER TABLE note_chunks
            ADD CONSTRAINT note_chunks_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
END $$;
"""


async def _create_database_if_missing() -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"Created database '{DB_NAME}'")
        else:
            print(f"Database '{DB_NAME}' already exists")
    finally:
        await conn.close()


async def _enable_extensions_and_migrate() -> None:
    conn = await asyncpg.connect(APP_URL)
    try:
        await conn.execute(EXTENSIONS)
        # MIGRATE runs after create_all (below) so legacy ALTERs apply against
        # tables that exist. But we run extensions here first so create_all
        # can use the vector type.
    finally:
        await conn.close()


async def _run_migrate() -> None:
    """Idempotent legacy ALTERs for older dev DBs."""
    conn = await asyncpg.connect(APP_URL)
    try:
        await conn.execute(MIGRATE)
    finally:
        await conn.close()


async def _create_all_models() -> None:
    """Apply the schema from SQLAlchemy models — every Base subclass becomes a table."""
    # Importing these registers every model's class on infra.db.Base.metadata.
    import infra.devices.models  # noqa: F401  — Device (infra-level device registry; user-keyed `devices` table)
    import infra.devices.canvas_layout  # noqa: F401  — CanvasLayout (infra device/canvas topology)
    import silicon_brain.models  # noqa: F401  — User, Profile, Document, DocumentChunk, UserPreferences
    import persona.teacher.models  # noqa: F401  — Interaction, LearningGoal, Recommendation, LearningSession, TeacherPreferenceModel, ConceptNode, ConceptEdge
    from infra.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Schema applied (SQLAlchemy create_all)")


async def main() -> None:
    await _create_database_if_missing()
    await _enable_extensions_and_migrate()
    await _create_all_models()
    await _run_migrate()
    print("init_db: done")


if __name__ == "__main__":
    asyncio.run(main())
