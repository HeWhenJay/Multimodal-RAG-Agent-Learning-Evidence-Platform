-- 新增 Agent 上下文压缩摘要表，Redis 失效后仍可从 PostgreSQL 恢复会话上下文。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_conversation_summary (
    id VARCHAR(120) PRIMARY KEY,
    task_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    summary_type VARCHAR(40) NOT NULL DEFAULT 'CONTEXT_COMPRESSION',
    covered_message_start_id VARCHAR(120),
    covered_message_end_id VARCHAR(120),
    covered_message_count INTEGER NOT NULL DEFAULT 0,
    raw_token_estimate INTEGER NOT NULL DEFAULT 0,
    compressed_token_estimate INTEGER NOT NULL DEFAULT 0,
    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_text TEXT NOT NULL,
    key_facts_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    compression_model VARCHAR(120),
    compression_prompt_version VARCHAR(80) NOT NULL DEFAULT 'agent-context-compression-v1',
    compression_version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(40) NOT NULL DEFAULT 'ACTIVE',
    diagnostics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_conversation_summary_task_status_updated
    ON learning_evidence.agent_conversation_summary(task_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_conversation_summary_user_task_status
    ON learning_evidence.agent_conversation_summary(user_id, task_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_conversation_summary_covered_range
    ON learning_evidence.agent_conversation_summary(task_id, covered_message_start_id, covered_message_end_id);

CREATE INDEX IF NOT EXISTS idx_agent_conversation_summary_summary_gin
    ON learning_evidence.agent_conversation_summary USING GIN (summary_json);

CREATE INDEX IF NOT EXISTS idx_agent_conversation_summary_key_facts_gin
    ON learning_evidence.agent_conversation_summary USING GIN (key_facts_json);
