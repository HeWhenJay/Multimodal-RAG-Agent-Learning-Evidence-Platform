CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

-- Ragas 效果评估必须使用生产同库 PostgreSQL/pgvector，并通过 Ragas_Test 前缀隔离评估资料。
CREATE TABLE IF NOT EXISTS "Ragas_Test_rag_document" (
    document_id VARCHAR(120) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    user_id VARCHAR(120) NOT NULL,
    visibility_scope VARCHAR(30) NOT NULL DEFAULT 'private',
    language VARCHAR(30) NOT NULL DEFAULT 'zh-CN',
    parser VARCHAR(80),
    document_summary TEXT,
    section_summaries JSONB NOT NULL DEFAULT '{}'::jsonb,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS "Ragas_Test_rag_chunk" (
    chunk_id VARCHAR(180) PRIMARY KEY,
    document_id VARCHAR(120) NOT NULL REFERENCES "Ragas_Test_rag_document"(document_id) ON DELETE CASCADE,
    chunk_position INTEGER NOT NULL,
    section_name VARCHAR(255) NOT NULL DEFAULT '全文',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    term_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding VECTOR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "idx_Ragas_Test_rag_document_type"
    ON "Ragas_Test_rag_document"(document_type);

CREATE INDEX IF NOT EXISTS "idx_Ragas_Test_rag_document_user_visibility"
    ON "Ragas_Test_rag_document"(user_id, visibility_scope);

CREATE INDEX IF NOT EXISTS "idx_Ragas_Test_rag_chunk_document_position"
    ON "Ragas_Test_rag_chunk"(document_id, chunk_position);

CREATE INDEX IF NOT EXISTS "idx_Ragas_Test_rag_chunk_metadata_gin"
    ON "Ragas_Test_rag_chunk" USING GIN (metadata);

CREATE INDEX IF NOT EXISTS "idx_Ragas_Test_rag_chunk_embedding_hnsw"
    ON "Ragas_Test_rag_chunk" USING hnsw (embedding vector_cosine_ops);
