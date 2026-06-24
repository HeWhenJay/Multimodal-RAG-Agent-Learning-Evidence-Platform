CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

-- Agent 第二阶段任务主表，Java 从登录态写入 user_id。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_task (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    task_type VARCHAR(40) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'CREATED',
    title VARCHAR(255),
    input_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '{}',
    draft_json TEXT NOT NULL DEFAULT '{}',
    final_json TEXT NOT NULL DEFAULT '{}',
    python_thread_id VARCHAR(160),
    error_code VARCHAR(120),
    error_message VARCHAR(1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_task_user_status_updated
    ON learning_evidence.agent_task(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_task_python_thread
    ON learning_evidence.agent_task(python_thread_id)
    WHERE python_thread_id IS NOT NULL;

-- Agent 工具调用记录，只读工具也必须留下脱敏观测记录。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_tool_call (
    id VARCHAR(120) PRIMARY KEY,
    task_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    tool_name VARCHAR(120) NOT NULL,
    tool_type VARCHAR(30) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'PENDING',
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    ownership_verified BOOLEAN NOT NULL DEFAULT FALSE,
    scope VARCHAR(80) NOT NULL DEFAULT 'current_user_or_authorized',
    error_code VARCHAR(120),
    error_message VARCHAR(1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_call_task_created
    ON learning_evidence.agent_tool_call(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_tool_call_status
    ON learning_evidence.agent_tool_call(status);

-- Human-in-the-Loop 审批记录，计划、CRUD、输出确认共用。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_human_review (
    id VARCHAR(120) PRIMARY KEY,
    task_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    review_type VARCHAR(30) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'PENDING',
    proposal_json TEXT NOT NULL DEFAULT '{}',
    decision_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by VARCHAR(120),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_human_review_task_status
    ON learning_evidence.agent_human_review(task_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_human_review_expires
    ON learning_evidence.agent_human_review(expires_at)
    WHERE expires_at IS NOT NULL;

-- 可撤销变更操作，幂等键按当前用户和资源范围唯一。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_operation (
    id VARCHAR(120) PRIMARY KEY,
    task_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_task(id) ON DELETE CASCADE,
    review_id VARCHAR(120) REFERENCES learning_evidence.agent_human_review(id) ON DELETE SET NULL,
    user_id VARCHAR(120) NOT NULL,
    operation_type VARCHAR(80) NOT NULL,
    resource_type VARCHAR(80) NOT NULL,
    resource_id VARCHAR(120) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'PENDING_APPROVAL',
    before_snapshot_ref VARCHAR(180),
    after_snapshot_ref VARCHAR(180),
    idempotency_key VARCHAR(160) NOT NULL,
    undo_deadline TIMESTAMPTZ,
    audit_event_id BIGINT,
    error_code VARCHAR(120),
    error_message VARCHAR(1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_agent_operation_idempotency
    ON learning_evidence.agent_operation(user_id, operation_type, resource_type, resource_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_agent_operation_task_status
    ON learning_evidence.agent_operation(task_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_operation_user_status
    ON learning_evidence.agent_operation(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_operation_resource
    ON learning_evidence.agent_operation(resource_type, resource_id);

-- 操作前后快照只保存当前用户资源的脱敏 JSON 或安全引用。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_operation_snapshot (
    id VARCHAR(120) PRIMARY KEY,
    operation_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_operation(id) ON DELETE CASCADE,
    snapshot_type VARCHAR(20) NOT NULL,
    resource_type VARCHAR(80) NOT NULL,
    resource_id VARCHAR(120) NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    content_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_operation_snapshot_operation
    ON learning_evidence.agent_operation_snapshot(operation_id, snapshot_type);
