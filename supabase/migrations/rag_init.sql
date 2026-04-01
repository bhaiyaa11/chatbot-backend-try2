-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create scripts table
CREATE TABLE IF NOT EXISTS scripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    client TEXT,
    business_unit TEXT,
    video_type TEXT,
    tone TEXT,
    hash TEXT UNIQUE, -- SHA-256 for deduplication
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 3. Create script_chunks table
CREATE TABLE IF NOT EXISTS script_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    script_id UUID REFERENCES scripts(id) ON DELETE CASCADE,
    type TEXT CHECK (type IN ('hook', 'cta', 'framework', 'insight', 'body')),
    content TEXT NOT NULL,
    embedding VECTOR(768), -- Matches text-embedding-004 (768-dim)
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create generations_log table
CREATE TABLE IF NOT EXISTS generations_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_params JSONB,
    output_script TEXT,
    retrieved_chunk_ids UUID[],
    sources JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Add HNSW index for fast vector search
-- Note: 'm' and 'ef_construction' are tuned for typical RAG workloads
CREATE INDEX IF NOT EXISTS script_chunks_embedding_idx ON script_chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 6. Enable Row Level Security (RLS) - Optional but recommended
ALTER TABLE scripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE script_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE generations_log ENABLE ROW LEVEL SECURITY;

-- 7. Add vector search RPC
CREATE OR REPLACE FUNCTION match_chunks (
  query_embedding vector(768),
  match_threshold float,
  match_count int,
  metadata_filter jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE (
  id uuid,
  script_id uuid,
  content text,
  type text,
  metadata jsonb,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    sc.id,
    sc.script_id,
    sc.content,
    sc.type,
    sc.metadata,
    1 - (sc.embedding <=> query_embedding) AS similarity
  FROM script_chunks sc
  JOIN scripts s ON sc.script_id = s.id
  WHERE 
    (metadata_filter ->> 'client' IS NULL OR s.client = metadata_filter ->> 'client') AND
    (metadata_filter ->> 'business_unit' IS NULL OR s.business_unit = metadata_filter ->> 'business_unit') AND
    (metadata_filter ->> 'video_type' IS NULL OR s.video_type = metadata_filter ->> 'video_type') AND
    1 - (sc.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

-- 8. Add simple read-all policies (adjust as needed for production)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'scripts') THEN
        CREATE POLICY "Allow service_role full access" ON scripts FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'script_chunks') THEN
        CREATE POLICY "Allow service_role full access" ON script_chunks FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'generations_log') THEN
        CREATE POLICY "Allow service_role full access" ON generations_log FOR ALL USING (true);
    END IF;
END $$;
