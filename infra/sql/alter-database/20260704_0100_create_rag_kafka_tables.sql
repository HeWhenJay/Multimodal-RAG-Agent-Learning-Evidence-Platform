ALTER TABLE learning_evidence.learning_material
    ADD COLUMN IF NOT EXISTS active_index_job_id VARCHAR(80);

ALTER TABLE learning_evidence.learning_material
    ADD COLUMN IF NOT EXISTS index_request_version INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_learning_material_active_index_job
    ON learning_evidence.learning_material(active_index_job_id);

CREATE TABLE IF NOT EXISTS learning_evidence.rag_index_job (
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

CREATE INDEX IF NOT EXISTS idx_rag_index_job_material_status
    ON learning_evidence.rag_index_job(material_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_index_job_document
    ON learning_evidence.rag_index_job(canonical_document_id, request_version);

CREATE TABLE IF NOT EXISTS learning_evidence.rag_outbox_event (
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

CREATE INDEX IF NOT EXISTS idx_rag_outbox_status_next
    ON learning_evidence.rag_outbox_event(status, next_attempt_at, id);

CREATE INDEX IF NOT EXISTS idx_rag_outbox_lease
    ON learning_evidence.rag_outbox_event(lease_until);

CREATE TABLE IF NOT EXISTS learning_evidence.rag_consumed_event (
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

CREATE INDEX IF NOT EXISTS idx_rag_consumed_job
    ON learning_evidence.rag_consumed_event(job_id, consumed_at DESC);
