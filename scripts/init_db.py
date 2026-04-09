"""Create the bewithme database and run the Phase 1 schema."""
import asyncio
import asyncpg


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS profile (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    self_description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL,
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
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
