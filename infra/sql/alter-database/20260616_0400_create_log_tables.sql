CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS log_event (
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
    ON log_event(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_event_domain_module
    ON log_event(domain, module);

CREATE INDEX IF NOT EXISTS idx_log_event_trace_id
    ON log_event(trace_id);

CREATE INDEX IF NOT EXISTS idx_log_event_material_id
    ON log_event(material_id);

CREATE TABLE IF NOT EXISTS log_error (
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
    ON log_error(fingerprint);

CREATE INDEX IF NOT EXISTS idx_log_error_created_at
    ON log_error(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_error_last_seen_at
    ON log_error(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_error_domain_module
    ON log_error(domain, module);

CREATE INDEX IF NOT EXISTS idx_log_error_status_severity
    ON log_error(status, severity);

CREATE INDEX IF NOT EXISTS idx_log_error_trace_id
    ON log_error(trace_id);

CREATE INDEX IF NOT EXISTS idx_log_error_material_id
    ON log_error(material_id);
