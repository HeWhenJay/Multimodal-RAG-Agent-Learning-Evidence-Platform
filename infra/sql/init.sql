CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
GRANT USAGE, CREATE ON SCHEMA learning_evidence TO postgres;
SET search_path TO learning_evidence, public;

-- 初始建表脚本会重建业务表，仅保留默认管理员种子数据。
DROP TABLE IF EXISTS learning_evidence."Ragas_Test_rag_chunk";
DROP TABLE IF EXISTS learning_evidence."Ragas_Test_rag_document";
DROP TABLE IF EXISTS learning_evidence.rag_chunk;
DROP TABLE IF EXISTS learning_evidence.rag_document;
DROP TABLE IF EXISTS learning_evidence.rag_consumed_event;
DROP TABLE IF EXISTS learning_evidence.rag_outbox_event;
DROP TABLE IF EXISTS learning_evidence.rag_index_job;
DROP TABLE IF EXISTS learning_evidence.agent_memory_audit;
DROP TABLE IF EXISTS learning_evidence.agent_memory_version;
DROP TABLE IF EXISTS learning_evidence.agent_memory_embedding;
DROP TABLE IF EXISTS learning_evidence.agent_memory_item;
DROP TABLE IF EXISTS learning_evidence.agent_operation_snapshot;
DROP TABLE IF EXISTS learning_evidence.agent_operation;
DROP TABLE IF EXISTS learning_evidence.agent_human_review;
DROP TABLE IF EXISTS learning_evidence.agent_tool_call;
DROP TABLE IF EXISTS learning_evidence.agent_cache_repair_task;
DROP TABLE IF EXISTS learning_evidence.agent_conversation_summary;
DROP TABLE IF EXISTS learning_evidence.agent_chat_message;
DROP TABLE IF EXISTS learning_evidence.agent_task;
DROP TABLE IF EXISTS learning_evidence.agent_conversation_folder;
DROP TABLE IF EXISTS learning_evidence.rag_query_history;
DROP TABLE IF EXISTS learning_evidence.log_error;
DROP TABLE IF EXISTS learning_evidence.log_event;
DROP TABLE IF EXISTS learning_evidence.learning_material;
DROP TABLE IF EXISTS learning_evidence.auth_login_record;
DROP TABLE IF EXISTS learning_evidence.auth_session;
DROP TABLE IF EXISTS learning_evidence.system_setting;
DROP TABLE IF EXISTS learning_evidence.app_user;

CREATE TABLE learning_evidence.app_user (
    id BIGSERIAL PRIMARY KEY,
    account VARCHAR(120) NOT NULL,
    email VARCHAR(160),
    display_name VARCHAR(80) NOT NULL,
    role VARCHAR(40) NOT NULL DEFAULT 'ADMIN',
    password_hash VARCHAR(128) NOT NULL,
    password_salt VARCHAR(128) NOT NULL,
    password_algorithm VARCHAR(40) NOT NULL DEFAULT 'PBKDF2WithHmacSHA256',
    password_iterations INTEGER NOT NULL DEFAULT 120000,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    last_login_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_app_user_account UNIQUE (account)
);

CREATE INDEX idx_app_user_status
    ON learning_evidence.app_user(status);

INSERT INTO learning_evidence.app_user (
    account,
    email,
    display_name,
    role,
    password_hash,
    password_salt,
    password_algorithm,
    password_iterations,
    status
)
VALUES (
    'admin',
    'admin@evidence.ai',
    '系统管理员',
    'ADMIN',
    '5d37928329dcaa2c3a5a2fe7dce62c3e7364bbe1d4a6dc5e6700ec968e0015a6',
    'learning-evidence-admin-salt-v1',
    'PBKDF2WithHmacSHA256',
    120000,
    'ACTIVE'
)
ON CONFLICT (account) DO UPDATE SET
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    password_hash = EXCLUDED.password_hash,
    password_salt = EXCLUDED.password_salt,
    password_algorithm = EXCLUDED.password_algorithm,
    password_iterations = EXCLUDED.password_iterations,
    status = EXCLUDED.status,
    updated_at = CURRENT_TIMESTAMP;

CREATE TABLE learning_evidence.auth_session (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES learning_evidence.app_user(id) ON DELETE CASCADE,
    token_hash VARCHAR(128) NOT NULL,
    remember_me BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_auth_session_token_hash UNIQUE (token_hash)
);

CREATE INDEX idx_auth_session_user_id
    ON learning_evidence.auth_session(user_id);

CREATE INDEX idx_auth_session_expires_at
    ON learning_evidence.auth_session(expires_at);

CREATE TABLE learning_evidence.auth_login_record (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES learning_evidence.app_user(id) ON DELETE SET NULL,
    account VARCHAR(120) NOT NULL,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255),
    ip_address VARCHAR(80),
    user_agent VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_auth_login_record_account
    ON learning_evidence.auth_login_record(account);

CREATE INDEX idx_auth_login_record_created_at
    ON learning_evidence.auth_login_record(created_at DESC);

CREATE TABLE learning_evidence.learning_material (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    user_id VARCHAR(120) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    status VARCHAR(30) NOT NULL,
    parser VARCHAR(80),
    document_summary TEXT,
    chunk_count INTEGER DEFAULT 0,
    original_filename VARCHAR(255),
    original_file_path VARCHAR(500),
    storage_type VARCHAR(30) NOT NULL DEFAULT 'local',
    object_key VARCHAR(700),
    public_url VARCHAR(700),
    active_index_job_id VARCHAR(80),
    index_request_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_learning_material_status
    ON learning_evidence.learning_material(status);

CREATE INDEX idx_learning_material_document_type
    ON learning_evidence.learning_material(document_type);

CREATE INDEX idx_learning_material_user_updated
    ON learning_evidence.learning_material(user_id, updated_at DESC);

CREATE INDEX idx_learning_material_active_index_job
    ON learning_evidence.learning_material(active_index_job_id);

CREATE TABLE learning_evidence.rag_index_job (
    id VARCHAR(80) PRIMARY KEY,
    material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    canonical_document_id VARCHAR(120) NOT NULL,
    staging_document_id VARCHAR(180) NOT NULL,
    user_id VARCHAR(120) NOT NULL,
    operation VARCHAR(40) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'REQUESTED',
    request_version INTEGER NOT NULL,
    idempotency_key VARCHAR(220) NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code VARCHAR(120),
    error_message VARCHAR(1000),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    indexed_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_rag_index_job_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX idx_rag_index_job_material_status
    ON learning_evidence.rag_index_job(material_id, status, updated_at DESC);

CREATE INDEX idx_rag_index_job_document
    ON learning_evidence.rag_index_job(canonical_document_id, request_version);

CREATE TABLE learning_evidence.rag_outbox_event (
    id BIGSERIAL PRIMARY KEY,
    topic VARCHAR(160) NOT NULL,
    message_key VARCHAR(180) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(220) NOT NULL,
    payload_json TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'NEW',
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_until TIMESTAMPTZ,
    locked_by VARCHAR(120),
    published_at TIMESTAMPTZ,
    error_message VARCHAR(1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_rag_outbox_topic_idempotency UNIQUE (topic, idempotency_key)
);

CREATE INDEX idx_rag_outbox_status_next
    ON learning_evidence.rag_outbox_event(status, next_attempt_at, id);

CREATE INDEX idx_rag_outbox_lease
    ON learning_evidence.rag_outbox_event(lease_until);

CREATE TABLE learning_evidence.rag_consumed_event (
    id BIGSERIAL PRIMARY KEY,
    consumer_name VARCHAR(120) NOT NULL,
    message_id VARCHAR(120) NOT NULL,
    message_type VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(220) NOT NULL,
    job_id VARCHAR(80),
    progress_sequence INTEGER,
    status VARCHAR(30) NOT NULL DEFAULT 'CONSUMED',
    error_message VARCHAR(1000),
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_rag_consumed_message UNIQUE (consumer_name, message_id),
    CONSTRAINT uk_rag_consumed_idempotency UNIQUE (consumer_name, message_type, idempotency_key)
);

CREATE INDEX idx_rag_consumed_job
    ON learning_evidence.rag_consumed_event(job_id, consumed_at DESC);

CREATE TABLE learning_evidence.rag_query_history (
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

CREATE UNIQUE INDEX uk_rag_query_history_task_id
    ON learning_evidence.rag_query_history(task_id)
    WHERE task_id IS NOT NULL;

CREATE INDEX idx_rag_query_history_user_created
    ON learning_evidence.rag_query_history(user_id, created_at DESC);

CREATE INDEX idx_rag_query_history_status
    ON learning_evidence.rag_query_history(status);

CREATE TABLE learning_evidence.log_event (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(80) NOT NULL,
    session_id VARCHAR(120),
    user_id VARCHAR(120) NOT NULL DEFAULT 'anonymous',
    source VARCHAR(30) NOT NULL,
    domain VARCHAR(50) NOT NULL DEFAULT 'system',
    level VARCHAR(20) NOT NULL DEFAULT 'INFO',
    module VARCHAR(80) NOT NULL,
    stage VARCHAR(80),
    event_type VARCHAR(80) NOT NULL,
    action VARCHAR(120) NOT NULL,
    message VARCHAR(500),
    route VARCHAR(255),
    http_method VARCHAR(20),
    request_path VARCHAR(500),
    status_code INTEGER,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    duration_ms INTEGER,
    material_id BIGINT,
    document_id VARCHAR(120),
    parser VARCHAR(80),
    client_time TIMESTAMPTZ,
    server_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_log_event_created_at
    ON learning_evidence.log_event(created_at DESC);

CREATE INDEX idx_log_event_domain_module
    ON learning_evidence.log_event(domain, module);

CREATE INDEX idx_log_event_trace_id
    ON learning_evidence.log_event(trace_id);

CREATE INDEX idx_log_event_material_id
    ON learning_evidence.log_event(material_id);

CREATE TABLE learning_evidence.log_error (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(80) NOT NULL,
    session_id VARCHAR(120),
    user_id VARCHAR(120) NOT NULL DEFAULT 'anonymous',
    source VARCHAR(30) NOT NULL,
    domain VARCHAR(50) NOT NULL DEFAULT 'system',
    severity VARCHAR(20) NOT NULL DEFAULT 'ERROR',
    module VARCHAR(80) NOT NULL,
    stage VARCHAR(80),
    action VARCHAR(120),
    error_type VARCHAR(120) NOT NULL,
    error_code VARCHAR(120),
    message VARCHAR(1000) NOT NULL,
    stack_trace TEXT,
    fingerprint VARCHAR(128) NOT NULL,
    route VARCHAR(255),
    http_method VARCHAR(20),
    request_path VARCHAR(500),
    status_code INTEGER,
    duration_ms INTEGER,
    material_id BIGINT,
    document_id VARCHAR(120),
    parser VARCHAR(80),
    client_time TIMESTAMPTZ,
    server_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    context_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX uk_log_error_fingerprint
    ON learning_evidence.log_error(fingerprint);

CREATE INDEX idx_log_error_created_at
    ON learning_evidence.log_error(created_at DESC);

CREATE INDEX idx_log_error_last_seen_at
    ON learning_evidence.log_error(last_seen_at DESC);

CREATE INDEX idx_log_error_domain_module
    ON learning_evidence.log_error(domain, module);

CREATE INDEX idx_log_error_status_severity
    ON learning_evidence.log_error(status, severity);

CREATE INDEX idx_log_error_trace_id
    ON learning_evidence.log_error(trace_id);

CREATE INDEX idx_log_error_material_id
    ON learning_evidence.log_error(material_id);

-- Agent 第二阶段任务、工具、审批和可撤销操作表。
CREATE TABLE learning_evidence.agent_conversation_folder (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    name VARCHAR(80) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_agent_conversation_folder_user_sort
    ON learning_evidence.agent_conversation_folder(user_id, sort_order, updated_at DESC);

CREATE TABLE learning_evidence.agent_task (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    folder_id VARCHAR(120) REFERENCES learning_evidence.agent_conversation_folder(id) ON DELETE SET NULL,
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

CREATE TABLE learning_evidence.agent_cache_repair_task (
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

CREATE INDEX idx_agent_cache_repair_status_next
    ON learning_evidence.agent_cache_repair_task(status, next_attempt_at);

CREATE INDEX idx_agent_task_user_status_updated
    ON learning_evidence.agent_task(user_id, status, updated_at DESC);

CREATE INDEX idx_agent_task_user_folder_updated
    ON learning_evidence.agent_task(user_id, folder_id, updated_at DESC);

CREATE INDEX idx_agent_task_python_thread
    ON learning_evidence.agent_task(python_thread_id)
    WHERE python_thread_id IS NOT NULL;

CREATE TABLE learning_evidence.agent_chat_message (
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

CREATE UNIQUE INDEX uk_agent_chat_message_dedupe
    ON learning_evidence.agent_chat_message(task_id, dedupe_key);

CREATE INDEX idx_agent_chat_message_task_created
    ON learning_evidence.agent_chat_message(task_id, created_at);

CREATE UNIQUE INDEX uk_agent_chat_message_task_sequence
    ON learning_evidence.agent_chat_message(task_id, sequence_no);

CREATE INDEX idx_agent_chat_message_user_updated
    ON learning_evidence.agent_chat_message(user_id, updated_at DESC);

CREATE TABLE learning_evidence.agent_conversation_summary (
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

CREATE INDEX idx_agent_conversation_summary_task_status_updated
    ON learning_evidence.agent_conversation_summary(task_id, status, updated_at DESC);

CREATE INDEX idx_agent_conversation_summary_user_task_status
    ON learning_evidence.agent_conversation_summary(user_id, task_id, status, updated_at DESC);

CREATE INDEX idx_agent_conversation_summary_covered_range
    ON learning_evidence.agent_conversation_summary(task_id, covered_message_start_id, covered_message_end_id);

CREATE INDEX idx_agent_conversation_summary_summary_gin
    ON learning_evidence.agent_conversation_summary USING GIN (summary_json);

CREATE INDEX idx_agent_conversation_summary_key_facts_gin
    ON learning_evidence.agent_conversation_summary USING GIN (key_facts_json);

CREATE TABLE learning_evidence.agent_tool_call (
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

CREATE INDEX idx_agent_tool_call_task_created
    ON learning_evidence.agent_tool_call(task_id, created_at);

CREATE INDEX idx_agent_tool_call_status
    ON learning_evidence.agent_tool_call(status);

CREATE TABLE learning_evidence.agent_human_review (
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

CREATE INDEX idx_agent_human_review_task_status
    ON learning_evidence.agent_human_review(task_id, status);

CREATE INDEX idx_agent_human_review_expires
    ON learning_evidence.agent_human_review(expires_at)
    WHERE expires_at IS NOT NULL;

CREATE TABLE learning_evidence.agent_operation (
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

CREATE UNIQUE INDEX uk_agent_operation_idempotency
    ON learning_evidence.agent_operation(user_id, operation_type, resource_type, resource_id, idempotency_key);

CREATE INDEX idx_agent_operation_task_status
    ON learning_evidence.agent_operation(task_id, status);

CREATE INDEX idx_agent_operation_user_status
    ON learning_evidence.agent_operation(user_id, status, updated_at DESC);

CREATE INDEX idx_agent_operation_resource
    ON learning_evidence.agent_operation(resource_type, resource_id);

CREATE TABLE learning_evidence.agent_operation_snapshot (
    id VARCHAR(120) PRIMARY KEY,
    operation_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.agent_operation(id) ON DELETE CASCADE,
    snapshot_type VARCHAR(20) NOT NULL,
    resource_type VARCHAR(80) NOT NULL,
    resource_id VARCHAR(120) NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    content_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_agent_operation_snapshot_operation
    ON learning_evidence.agent_operation_snapshot(operation_id, snapshot_type);

CREATE TABLE learning_evidence.agent_memory_item (
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

CREATE INDEX idx_agent_memory_item_user_status_updated
    ON learning_evidence.agent_memory_item(user_id, status, updated_at DESC);

CREATE INDEX idx_agent_memory_item_lookup
    ON learning_evidence.agent_memory_item(user_id, namespace, subject_key, scope_type, status);

CREATE INDEX idx_agent_memory_item_source_task
    ON learning_evidence.agent_memory_item(source_task_id);

CREATE TABLE learning_evidence.agent_memory_embedding (
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_agent_memory_embedding_chunk UNIQUE (memory_id, chunk_id)
);

CREATE INDEX idx_agent_memory_embedding_memory
    ON learning_evidence.agent_memory_embedding(memory_id);

CREATE INDEX idx_agent_memory_embedding_user_status
    ON learning_evidence.agent_memory_embedding(user_id, status, deleted_at);

CREATE INDEX idx_agent_memory_embedding_metadata_gin
    ON learning_evidence.agent_memory_embedding USING GIN (metadata);

CREATE INDEX idx_agent_memory_embedding_hnsw
    ON learning_evidence.agent_memory_embedding USING hnsw (embedding vector_cosine_ops);

CREATE TABLE learning_evidence.agent_memory_version (
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

CREATE INDEX idx_agent_memory_version_memory
    ON learning_evidence.agent_memory_version(memory_id, created_at DESC);

CREATE INDEX idx_agent_memory_version_previous
    ON learning_evidence.agent_memory_version(previous_memory_id);

CREATE TABLE learning_evidence.agent_memory_audit (
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

CREATE INDEX idx_agent_memory_audit_memory_created
    ON learning_evidence.agent_memory_audit(memory_id, created_at DESC);

CREATE INDEX idx_agent_memory_audit_user_created
    ON learning_evidence.agent_memory_audit(user_id, created_at DESC);

CREATE TABLE learning_evidence.system_setting (
    setting_key VARCHAR(120) PRIMARY KEY,
    setting_group VARCHAR(80) NOT NULL,
    label VARCHAR(120) NOT NULL,
    setting_value VARCHAR(500) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE learning_evidence.rag_document (
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

CREATE TABLE learning_evidence.rag_chunk (
    chunk_id VARCHAR(180) PRIMARY KEY,
    document_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.rag_document(document_id) ON DELETE CASCADE,
    chunk_position INTEGER NOT NULL,
    section_name VARCHAR(255) NOT NULL DEFAULT '全文',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    term_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding VECTOR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rag_document_type
    ON learning_evidence.rag_document(document_type);

CREATE INDEX idx_rag_document_user_visibility
    ON learning_evidence.rag_document(user_id, visibility_scope);

CREATE INDEX idx_rag_chunk_document_position
    ON learning_evidence.rag_chunk(document_id, chunk_position);

CREATE INDEX idx_rag_chunk_metadata_gin
    ON learning_evidence.rag_chunk USING GIN (metadata);

CREATE INDEX idx_rag_chunk_embedding_hnsw
    ON learning_evidence.rag_chunk USING hnsw (embedding vector_cosine_ops);

-- Ragas 效果评估使用生产同库 PostgreSQL/pgvector，仅通过 Ragas_Test 前缀隔离数据。
CREATE TABLE learning_evidence."Ragas_Test_rag_document" (
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

CREATE TABLE learning_evidence."Ragas_Test_rag_chunk" (
    chunk_id VARCHAR(180) PRIMARY KEY,
    document_id VARCHAR(120) NOT NULL REFERENCES learning_evidence."Ragas_Test_rag_document"(document_id) ON DELETE CASCADE,
    chunk_position INTEGER NOT NULL,
    section_name VARCHAR(255) NOT NULL DEFAULT '全文',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    term_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding VECTOR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX "idx_Ragas_Test_rag_document_type"
    ON learning_evidence."Ragas_Test_rag_document"(document_type);

CREATE INDEX "idx_Ragas_Test_rag_document_user_visibility"
    ON learning_evidence."Ragas_Test_rag_document"(user_id, visibility_scope);

CREATE INDEX "idx_Ragas_Test_rag_chunk_document_position"
    ON learning_evidence."Ragas_Test_rag_chunk"(document_id, chunk_position);

CREATE INDEX "idx_Ragas_Test_rag_chunk_metadata_gin"
    ON learning_evidence."Ragas_Test_rag_chunk" USING GIN (metadata);

CREATE INDEX "idx_Ragas_Test_rag_chunk_embedding_hnsw"
    ON learning_evidence."Ragas_Test_rag_chunk" USING hnsw (embedding vector_cosine_ops);
