-- Trinity pgvector setup — run once in Supabase SQL editor
-- Enables semantic search over the shelf.

-- 1. Extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Shelf table (replaces JSONB shelf column in profiles)
CREATE TABLE IF NOT EXISTS trinity_shelf (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id  uuid REFERENCES profiles(id) ON DELETE CASCADE,
    topic       text NOT NULL,
    context     text DEFAULT '',
    status      text DEFAULT 'shelf',   -- shelf | on_hold | woven
    embedding   vector(384),
    added_at    timestamptz DEFAULT now(),
    updated_at  timestamptz DEFAULT now()
);

ALTER TABLE trinity_shelf ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON trinity_shelf FOR ALL USING (true);

-- 3. Cosine similarity search — called by query_shelf() in memory.py
CREATE OR REPLACE FUNCTION search_shelf(
    p_profile_id       uuid,
    p_query_embedding  vector(384),
    p_match_count      int     DEFAULT 6,
    p_status           text    DEFAULT 'shelf'
)
RETURNS TABLE (
    topic      text,
    context    text,
    status     text,
    added_at   timestamptz,
    similarity float
)
LANGUAGE sql
AS $$
    SELECT
        topic,
        context,
        status,
        added_at,
        1 - (embedding <=> p_query_embedding) AS similarity
    FROM trinity_shelf
    WHERE profile_id = p_profile_id
      AND status     = p_status
      AND embedding  IS NOT NULL
    ORDER BY embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- After running this:
-- 1. Run: python scripts/migrate_shelf.py   (migrates existing JSONB shelf to new table)
-- 2. Restart the runner / widget — shelf reads now come from trinity_shelf
