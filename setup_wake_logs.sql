-- Trinity wake_logs table — run once in Supabase SQL editor
-- Automatic cycle tracing. No self-reporting required from Trinity.

CREATE TABLE IF NOT EXISTS wake_logs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id         uuid REFERENCES profiles(id) ON DELETE CASCADE,
    mode               text DEFAULT 'cycle',
    started_at         timestamptz NOT NULL,
    ended_at           timestamptz,
    tool_calls         jsonb DEFAULT '[]',
    iterations         int DEFAULT 0,
    tokens_in          int DEFAULT 0,
    tokens_out         int DEFAULT 0,
    tokens_cache_write int DEFAULT 0,
    tokens_cache_read  int DEFAULT 0,
    notes              text,   -- Trinity-authored narrative, optional
    created_at         timestamptz DEFAULT now()
);

ALTER TABLE wake_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON wake_logs FOR ALL USING (true);
