"""Create the bewithme database and run the schema."""
import asyncio
import asyncpg


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profile (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    self_description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL,
    parent_interaction_id UUID REFERENCES interactions(id) ON DELETE SET NULL,
    title VARCHAR(200),
    passage_text TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source_document TEXT,
    embedding vector(768),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id);
CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at DESC);
-- idx_interactions_parent is created in MIGRATE, after the column is added
-- on existing databases.

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    filename TEXT,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON document_chunks(document_id);

CREATE TABLE IF NOT EXISTS learning_preferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    explanation_style TEXT NOT NULL DEFAULT 'balanced',
    depth_preference TEXT NOT NULL DEFAULT 'moderate',
    analogy_affinity TEXT NOT NULL DEFAULT 'moderate',
    math_comfort TEXT NOT NULL DEFAULT 'moderate',
    pacing TEXT NOT NULL DEFAULT 'moderate',
    meta_notes TEXT NOT NULL DEFAULT '',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_distilled_at TIMESTAMPTZ,
    preference_embedding vector(768),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS concept_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL DEFAULT 'new',
    encounter_count INTEGER NOT NULL DEFAULT 1,
    half_life_hours DOUBLE PRECISION NOT NULL DEFAULT 24.0,
    last_recalled_at TIMESTAMPTZ,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_concept_nodes_name ON concept_nodes(name);
CREATE INDEX IF NOT EXISTS idx_concept_nodes_last_seen ON concept_nodes(last_seen DESC);

CREATE TABLE IF NOT EXISTS concept_edges (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES concept_nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES concept_nodes(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL DEFAULT 'temporal',
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    context TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_reinforced TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

MIGRATE = """
-- Add HLR columns to existing concept_nodes tables
ALTER TABLE concept_nodes ADD COLUMN IF NOT EXISTS half_life_hours DOUBLE PRECISION NOT NULL DEFAULT 24.0;
ALTER TABLE concept_nodes ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ;

-- Add preference embedding to existing learning_preferences tables
ALTER TABLE learning_preferences ADD COLUMN IF NOT EXISTS preference_embedding vector(768);

-- Recursive question: parent link + title for each interaction
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS parent_interaction_id UUID REFERENCES interactions(id) ON DELETE SET NULL;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS title VARCHAR(200);
CREATE INDEX IF NOT EXISTS idx_interactions_parent ON interactions(parent_interaction_id);

-- Multi-tenancy migration: ensure users table exists and has default user
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO users (id, username) VALUES ('00000000-0000-0000-0000-000000000000', 'default')
    ON CONFLICT (username) DO NOTHING;

-- Add user_id columns with default for backfill, then make NOT NULL
DO $$
BEGIN
    -- profile
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='profile' AND column_name='user_id') THEN
        ALTER TABLE profile ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE profile SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE profile ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE profile ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_profile_user ON profile(user_id);
        ALTER TABLE profile ADD CONSTRAINT profile_user_id_key UNIQUE (user_id);
    END IF;

    -- interactions
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='interactions' AND column_name='user_id') THEN
        ALTER TABLE interactions ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE interactions SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE interactions ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE interactions ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);
    END IF;

    -- documents
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='user_id') THEN
        ALTER TABLE documents ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE documents SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE documents ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE documents ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
    END IF;

    -- learning_preferences
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='learning_preferences' AND column_name='user_id') THEN
        ALTER TABLE learning_preferences ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE learning_preferences SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE learning_preferences ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE learning_preferences ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_learning_preferences_user ON learning_preferences(user_id);
        ALTER TABLE learning_preferences ADD CONSTRAINT learning_preferences_user_id_key UNIQUE (user_id);
    END IF;

    -- concept_nodes
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='concept_nodes' AND column_name='user_id') THEN
        ALTER TABLE concept_nodes ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE concept_nodes SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE concept_nodes ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE concept_nodes ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_concept_nodes_user ON concept_nodes(user_id);
        -- Replace the old unique constraint on name with composite (user_id, name)
        ALTER TABLE concept_nodes DROP CONSTRAINT IF EXISTS concept_nodes_name_key;
        DROP INDEX IF EXISTS concept_nodes_name_key;
        ALTER TABLE concept_nodes ADD CONSTRAINT concept_nodes_user_id_name_key UNIQUE (user_id, name);
    END IF;

    -- concept_edges
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='concept_edges' AND column_name='user_id') THEN
        ALTER TABLE concept_edges ADD COLUMN user_id UUID DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES users(id) ON DELETE CASCADE;
        UPDATE concept_edges SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL;
        ALTER TABLE concept_edges ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE concept_edges ALTER COLUMN user_id DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_concept_edges_user ON concept_edges(user_id);
    END IF;
END $$;
"""


async def main():
    # Connect to default 'postgres' DB to create our database
    try:
        conn = await asyncpg.connect("postgresql://weng@localhost/postgres")
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'bewithme'")
        if not exists:
            await conn.execute("CREATE DATABASE bewithme")
            print("Created database 'bewithme'")
        else:
            print("Database 'bewithme' already exists")
        await conn.close()
    except Exception as e:
        print(f"Error creating database: {e}")
        return

    # Connect to bewithme and run schema
    conn = await asyncpg.connect("postgresql://weng@localhost/bewithme")
    await conn.execute(SCHEMA)
    print("Schema created successfully")

    # Run migrations for existing databases
    await conn.execute(MIGRATE)
    print("Migrations applied")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
