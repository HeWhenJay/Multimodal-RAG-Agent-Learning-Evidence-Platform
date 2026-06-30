-- 新增 Agent 历史聊天消息投影表，持久化用户输入、Agent 进度、工具观测、审批和最终回答。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_chat_message (
    id VARCHAR(120) PRIMARY KEY,
    task_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    sequence_no BIGINT NOT NULL DEFAULT 0,
    role VARCHAR(30) NOT NULL,
    message_type VARCHAR(60) NOT NULL,
    content TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_event_type VARCHAR(80),
    source_id VARCHAR(160),
    dedupe_key VARCHAR(220) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_agent_chat_message_dedupe
    ON learning_evidence.agent_chat_message(task_id, dedupe_key);

CREATE INDEX IF NOT EXISTS idx_agent_chat_message_task_created
    ON learning_evidence.agent_chat_message(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_message_user_updated
    ON learning_evidence.agent_chat_message(user_id, updated_at DESC);
