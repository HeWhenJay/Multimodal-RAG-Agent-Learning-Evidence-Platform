SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS learning_evidence.rag_query_history (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    task_id VARCHAR(120),
    question TEXT NOT NULL,
    answer TEXT,
    status VARCHAR(30) NOT NULL,
    top_k INTEGER NOT NULL DEFAULT 5,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    expanded_queries_json TEXT NOT NULL DEFAULT '[]',
    evidences_json TEXT NOT NULL DEFAULT '[]',
    diagnostics_json TEXT NOT NULL DEFAULT '{}',
    progress_events_json TEXT NOT NULL DEFAULT '[]',
    error_message VARCHAR(1000),
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_rag_query_history_task_id
    ON learning_evidence.rag_query_history(task_id)
    WHERE task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rag_query_history_user_created
    ON learning_evidence.rag_query_history(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_query_history_status
    ON learning_evidence.rag_query_history(status);
