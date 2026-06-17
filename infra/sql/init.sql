CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
GRANT USAGE, CREATE ON SCHEMA learning_evidence TO postgres;
SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS learning_evidence.app_user (
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

CREATE INDEX IF NOT EXISTS idx_app_user_status
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
    'admin@evidence.ai',
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

CREATE TABLE IF NOT EXISTS learning_evidence.auth_session (
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

CREATE INDEX IF NOT EXISTS idx_auth_session_user_id
    ON learning_evidence.auth_session(user_id);

CREATE INDEX IF NOT EXISTS idx_auth_session_expires_at
    ON learning_evidence.auth_session(expires_at);

CREATE TABLE IF NOT EXISTS learning_evidence.auth_login_record (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES learning_evidence.app_user(id) ON DELETE SET NULL,
    account VARCHAR(120) NOT NULL,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255),
    ip_address VARCHAR(80),
    user_agent VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auth_login_record_account
    ON learning_evidence.auth_login_record(account);

CREATE INDEX IF NOT EXISTS idx_auth_login_record_created_at
    ON learning_evidence.auth_login_record(created_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_material (
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_material_status
    ON learning_evidence.learning_material(status);

CREATE INDEX IF NOT EXISTS idx_learning_material_document_type
    ON learning_evidence.learning_material(document_type);

CREATE INDEX IF NOT EXISTS idx_learning_material_user_updated
    ON learning_evidence.learning_material(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.log_event (
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

CREATE INDEX IF NOT EXISTS idx_log_event_created_at
    ON learning_evidence.log_event(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_event_domain_module
    ON learning_evidence.log_event(domain, module);

CREATE INDEX IF NOT EXISTS idx_log_event_trace_id
    ON learning_evidence.log_event(trace_id);

CREATE INDEX IF NOT EXISTS idx_log_event_material_id
    ON learning_evidence.log_event(material_id);

CREATE TABLE IF NOT EXISTS learning_evidence.log_error (
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

CREATE UNIQUE INDEX IF NOT EXISTS uk_log_error_fingerprint
    ON learning_evidence.log_error(fingerprint);

CREATE INDEX IF NOT EXISTS idx_log_error_created_at
    ON learning_evidence.log_error(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_error_last_seen_at
    ON learning_evidence.log_error(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_error_domain_module
    ON learning_evidence.log_error(domain, module);

CREATE INDEX IF NOT EXISTS idx_log_error_status_severity
    ON learning_evidence.log_error(status, severity);

CREATE INDEX IF NOT EXISTS idx_log_error_trace_id
    ON learning_evidence.log_error(trace_id);

CREATE INDEX IF NOT EXISTS idx_log_error_material_id
    ON learning_evidence.log_error(material_id);

CREATE TABLE IF NOT EXISTS learning_evidence.video_slice (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    topic VARCHAR(255) NOT NULL,
    start_time VARCHAR(20) NOT NULL,
    end_time VARCHAR(20) NOT NULL,
    status VARCHAR(80) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_video_slice_title_start UNIQUE (title, start_time)
);

CREATE INDEX IF NOT EXISTS idx_video_slice_updated_at
    ON learning_evidence.video_slice(updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.resume_evidence_alignment (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    requirement VARCHAR(255) NOT NULL,
    evidence TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_resume_evidence_user_requirement UNIQUE (user_id, requirement)
);

CREATE INDEX IF NOT EXISTS idx_resume_evidence_status
    ON learning_evidence.resume_evidence_alignment(status);

CREATE INDEX IF NOT EXISTS idx_resume_evidence_user_updated
    ON learning_evidence.resume_evidence_alignment(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.jd_analysis_report (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    report_key VARCHAR(80) NOT NULL,
    job_description TEXT NOT NULL,
    match_score INTEGER NOT NULL DEFAULT 0,
    mastered_percent INTEGER NOT NULL DEFAULT 0,
    partial_percent INTEGER NOT NULL DEFAULT 0,
    gap_percent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_analysis_report_key UNIQUE (report_key)
);

CREATE TABLE IF NOT EXISTS learning_evidence.jd_analysis_skill (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES learning_evidence.jd_analysis_report(id) ON DELETE CASCADE,
    skill_name VARCHAR(160) NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_analysis_skill UNIQUE (report_id, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_jd_analysis_report_user_updated
    ON learning_evidence.jd_analysis_report(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.jd_learning_plan_item (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES learning_evidence.jd_analysis_report(id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_learning_plan_item UNIQUE (report_id, step_no)
);

CREATE TABLE IF NOT EXISTS learning_evidence.system_setting (
    setting_key VARCHAR(120) PRIMARY KEY,
    setting_group VARCHAR(80) NOT NULL,
    label VARCHAR(120) NOT NULL,
    setting_value VARCHAR(500) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_evidence.rag_document (
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

CREATE TABLE IF NOT EXISTS learning_evidence.rag_chunk (
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

CREATE INDEX IF NOT EXISTS idx_rag_document_type
    ON learning_evidence.rag_document(document_type);

CREATE INDEX IF NOT EXISTS idx_rag_document_user_visibility
    ON learning_evidence.rag_document(user_id, visibility_scope);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_document_position
    ON learning_evidence.rag_chunk(document_id, chunk_position);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_metadata_gin
    ON learning_evidence.rag_chunk USING GIN (metadata);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_embedding_hnsw
    ON learning_evidence.rag_chunk USING hnsw (embedding vector_cosine_ops);
