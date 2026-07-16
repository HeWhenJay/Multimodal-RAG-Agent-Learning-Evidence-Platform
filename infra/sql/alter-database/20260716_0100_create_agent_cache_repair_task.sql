-- 数据库提交成功但 Redis 上下文缓存删除失败时，用于持久化重试修复。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_cache_repair_task (
    task_id VARCHAR(120) PRIMARY KEY REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error VARCHAR(1000),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_cache_repair_status_next
    ON learning_evidence.agent_cache_repair_task(status, next_attempt_at);
