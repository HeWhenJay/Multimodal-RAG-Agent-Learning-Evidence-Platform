-- 纯 Python RAG 控制面：本地索引租约与查询耐久任务。
ALTER TABLE learning_evidence.rag_index_job
    ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(16) NOT NULL DEFAULT 'KAFKA';

ALTER TABLE learning_evidence.rag_index_job
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE learning_evidence.rag_index_job
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;

ALTER TABLE learning_evidence.rag_index_job
    ADD COLUMN IF NOT EXISTS locked_by VARCHAR(120);

CREATE INDEX IF NOT EXISTS idx_rag_index_job_local_claim
    ON learning_evidence.rag_index_job(delivery_mode, status, next_attempt_at, lease_until, requested_at);

CREATE TABLE IF NOT EXISTS learning_evidence.rag_query_task (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    query_history_id BIGINT NOT NULL REFERENCES learning_evidence.rag_query_history(id) ON DELETE CASCADE,
    status VARCHAR(30) NOT NULL DEFAULT 'REQUESTED',
    request_json TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_until TIMESTAMPTZ,
    locked_by VARCHAR(120),
    expires_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message VARCHAR(1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_rag_query_task_history UNIQUE (query_history_id)
);

CREATE INDEX IF NOT EXISTS idx_rag_query_task_claim
    ON learning_evidence.rag_query_task(status, next_attempt_at, lease_until, expires_at, created_at);

CREATE INDEX IF NOT EXISTS idx_rag_query_task_user
    ON learning_evidence.rag_query_task(user_id, created_at DESC);
