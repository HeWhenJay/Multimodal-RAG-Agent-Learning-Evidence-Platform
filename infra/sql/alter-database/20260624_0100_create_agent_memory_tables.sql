CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE IF NOT EXISTS learning_evidence.agent_memory_item (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    memory_type VARCHAR(40) NOT NULL,
    namespace VARCHAR(80) NOT NULL,
    scope_type VARCHAR(30) NOT NULL,
    scope_id VARCHAR(120),
    subject_key VARCHAR(120) NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    source_task_id VARCHAR(120) REFERENCES learning_evidence.agent_task(id) ON DELETE SET NULL,
    source_tool_call_id VARCHAR(120) REFERENCES learning_evidence.agent_tool_call(id) ON DELETE SET NULL,
    source_review_id VARCHAR(120) REFERENCES learning_evidence.agent_human_review(id) ON DELETE SET NULL,
    source_hash VARCHAR(128) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW',
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    importance NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    sensitivity_level VARCHAR(20) NOT NULL DEFAULT 'LOW',
    consent_source VARCHAR(40) NOT NULL DEFAULT 'AGENT_INFERRED',
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_item_user_status_updated
    ON learning_evidence.agent_memory_item(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memory_item_lookup
    ON learning_evidence.agent_memory_item(user_id, namespace, subject_key, scope_type, status);

CREATE INDEX IF NOT EXISTS idx_agent_memory_item_source_task
    ON learning_evidence.agent_memory_item(source_task_id);

CREATE TABLE IF NOT EXISTS learning_evidence.agent_memory_embedding (
    id VARCHAR(120) PRIMARY KEY,
    memory_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_memory_item(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    chunk_id VARCHAR(180) NOT NULL,
    retrieval_text TEXT NOT NULL,
    term_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding VECTOR(1024) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(40) NOT NULL DEFAULT 'ACTIVE',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_agent_memory_embedding_chunk
    ON learning_evidence.agent_memory_embedding(memory_id, chunk_id);

CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding_memory
    ON learning_evidence.agent_memory_embedding(memory_id);

CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding_user_status
    ON learning_evidence.agent_memory_embedding(user_id, status, deleted_at);

CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding_metadata_gin
    ON learning_evidence.agent_memory_embedding USING GIN (metadata);

CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding_hnsw
    ON learning_evidence.agent_memory_embedding USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS learning_evidence.agent_memory_version (
    id VARCHAR(120) PRIMARY KEY,
    memory_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_memory_item(id) ON DELETE CASCADE,
    previous_memory_id VARCHAR(120) REFERENCES learning_evidence.agent_memory_item(id) ON DELETE SET NULL,
    relation_type VARCHAR(40) NOT NULL,
    decision VARCHAR(40) NOT NULL,
    reason VARCHAR(1000),
    decided_by VARCHAR(60) NOT NULL,
    user_id VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_version_memory
    ON learning_evidence.agent_memory_version(memory_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memory_version_previous
    ON learning_evidence.agent_memory_version(previous_memory_id);

CREATE TABLE IF NOT EXISTS learning_evidence.agent_memory_audit (
    id VARCHAR(120) PRIMARY KEY,
    memory_id VARCHAR(120) REFERENCES learning_evidence.agent_memory_item(id) ON DELETE SET NULL,
    user_id VARCHAR(120) NOT NULL,
    task_id VARCHAR(120),
    action VARCHAR(60) NOT NULL,
    actor_type VARCHAR(60) NOT NULL,
    before_hash VARCHAR(128),
    after_hash VARCHAR(128),
    summary VARCHAR(1000) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_audit_memory_created
    ON learning_evidence.agent_memory_audit(memory_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memory_audit_user_created
    ON learning_evidence.agent_memory_audit(user_id, created_at DESC);
