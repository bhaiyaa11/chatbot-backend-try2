-- ============================================================
-- Migration: Conversational Memory System
-- Transforms the chatbot from stateless to stateful with
-- persistent conversation memory, summarization, and vector search.
-- 
-- Prerequisites: pgvector extension (already enabled via rag_init.sql)
-- ============================================================

-- ============================================================
-- TABLE: conversations
-- The anchor for all conversation state. Replaces in-memory _sessions.
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    message_count   INT DEFAULT 0,
    is_archived     BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated 
    ON conversations(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_last_message 
    ON conversations(last_message_at DESC);

-- ============================================================
-- ALTER: messages table (additive only — no existing data lost)
-- Adds conversation linkage and structured metadata columns.
-- The existing chat_id column is reused as the foreign key.
-- ============================================================
DO $$
BEGIN
    -- Add conversation_id column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'messages' AND column_name = 'conversation_id'
    ) THEN
        ALTER TABLE messages ADD COLUMN conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE;
    END IF;

    -- Add message_type column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'messages' AND column_name = 'message_type'
    ) THEN
        ALTER TABLE messages ADD COLUMN message_type TEXT DEFAULT 'text';
    END IF;

    -- Add metadata column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'messages' AND column_name = 'metadata'
    ) THEN
        ALTER TABLE messages ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb;
    END IF;

    -- Add token_count column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'messages' AND column_name = 'token_count'
    ) THEN
        ALTER TABLE messages ADD COLUMN token_count INT DEFAULT 0;
    END IF;
END $$;

-- Index for fast context window retrieval (the most critical query path)
CREATE INDEX IF NOT EXISTS idx_messages_conversation_time 
    ON messages(conversation_id, created_at DESC);

-- ============================================================
-- TABLE: conversation_summaries
-- Long-term memory via progressive summarization.
-- When message count exceeds threshold, older messages are
-- compressed into summaries. Recent messages stay verbatim.
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    summary             TEXT NOT NULL,
    message_range_start TIMESTAMPTZ NOT NULL,
    message_range_end   TIMESTAMPTZ NOT NULL,
    messages_covered    INT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation 
    ON conversation_summaries(conversation_id, created_at DESC);

-- ============================================================
-- TABLE: message_embeddings
-- Vector memory for semantic retrieval across conversations.
-- Uses same 768-dim embeddings as existing script_chunks table.
-- ============================================================
CREATE TABLE IF NOT EXISTS message_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content_type    TEXT NOT NULL CHECK (content_type IN ('user_prompt', 'generated_script', 'edit_instruction')),
    embedding       VECTOR(768),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_embeddings_vector 
    ON message_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_message_embeddings_conversation 
    ON message_embeddings(conversation_id);

-- ============================================================
-- TABLE: research_briefs
-- Replaces in-memory _research_cache with persistent storage.
-- Briefs are referenced by a short 12-char ID in the API.
-- ============================================================
CREATE TABLE IF NOT EXISTS research_briefs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    short_id    TEXT UNIQUE NOT NULL,
    data        JSONB NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_research_briefs_short_id 
    ON research_briefs(short_id);

-- ============================================================
-- RPC: match_message_embeddings
-- Semantic search across conversation message history.
-- Optionally scoped to a single conversation.
-- ============================================================
CREATE OR REPLACE FUNCTION match_message_embeddings(
    query_embedding VECTOR(768),
    match_threshold FLOAT,
    match_count INT,
    target_conversation_id UUID DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    message_id UUID,
    conversation_id UUID,
    content_type TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        me.id,
        me.message_id,
        me.conversation_id,
        me.content_type,
        1 - (me.embedding <=> query_embedding) AS similarity
    FROM message_embeddings me
    WHERE
        (target_conversation_id IS NULL OR me.conversation_id = target_conversation_id)
        AND 1 - (me.embedding <=> query_embedding) > match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;

-- ============================================================
-- Trigger: auto-update conversations.updated_at on message insert
-- ============================================================
CREATE OR REPLACE FUNCTION update_conversation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE conversations 
    SET updated_at = NOW(), 
        last_message_at = NOW(),
        message_count = message_count + 1
    WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_conversation_on_message ON messages;
CREATE TRIGGER trg_update_conversation_on_message
    AFTER INSERT ON messages
    FOR EACH ROW
    WHEN (NEW.conversation_id IS NOT NULL)
    EXECUTE FUNCTION update_conversation_timestamp();

-- ============================================================
-- RLS Policies — service_role full access
-- ============================================================
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_briefs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'conversations') THEN
        CREATE POLICY "Allow service_role full access" ON conversations FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'conversation_summaries') THEN
        CREATE POLICY "Allow service_role full access" ON conversation_summaries FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'message_embeddings') THEN
        CREATE POLICY "Allow service_role full access" ON message_embeddings FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service_role full access' AND tablename = 'research_briefs') THEN
        CREATE POLICY "Allow service_role full access" ON research_briefs FOR ALL USING (true);
    END IF;
END $$;
