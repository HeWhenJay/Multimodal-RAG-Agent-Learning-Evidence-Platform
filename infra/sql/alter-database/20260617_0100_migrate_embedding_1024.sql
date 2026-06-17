CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

-- 128 维 hash embedding 不能转换为 1024 维百炼 embedding。
-- 迁移时清空旧向量切块，保留 Java 资料记录，并将资料状态标记为需要重建索引。
DROP INDEX IF EXISTS idx_rag_chunk_embedding_hnsw;

TRUNCATE TABLE rag_chunk;
DELETE FROM rag_document;

ALTER TABLE rag_chunk
    DROP COLUMN IF EXISTS embedding;

ALTER TABLE rag_chunk
    ADD COLUMN IF NOT EXISTS embedding VECTOR(1024) NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rag_chunk_embedding_hnsw
    ON rag_chunk USING hnsw (embedding vector_cosine_ops);

UPDATE learning_material
SET status = 'REINDEXING',
    chunk_count = 0,
    updated_at = CURRENT_TIMESTAMP
WHERE status IN ('READY', 'PARTIAL', 'PARSING', 'PENDING');
