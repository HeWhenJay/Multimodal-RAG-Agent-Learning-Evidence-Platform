-- 学习资料关键知识点、FSRS 状态、评分日志与用户提醒设置。
CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_setting (
    user_id VARCHAR(120) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    desired_retention DOUBLE PRECISION NOT NULL DEFAULT 0.90,
    daily_limit INTEGER NOT NULL DEFAULT 20,
    reminder_time TIME NOT NULL DEFAULT '09:00',
    timezone VARCHAR(80) NOT NULL DEFAULT 'Asia/Shanghai',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_learning_review_retention CHECK (desired_retention BETWEEN 0.80 AND 0.97),
    CONSTRAINT ck_learning_review_daily_limit CHECK (daily_limit BETWEEN 1 AND 100)
);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_material (
    id BIGSERIAL PRIMARY KEY,
    material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    index_request_version INTEGER NOT NULL DEFAULT 0,
    is_learning_content BOOLEAN,
    category VARCHAR(80),
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    reason VARCHAR(500),
    extractor VARCHAR(40),
    card_count INTEGER NOT NULL DEFAULT 0,
    generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_learning_review_material UNIQUE (material_id),
    CONSTRAINT ck_learning_review_material_status CHECK (status IN ('PENDING', 'GENERATED', 'SKIPPED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_learning_review_material_user_status
    ON learning_evidence.learning_review_material(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_review_material_sync
    ON learning_evidence.learning_review_material(user_id, index_request_version);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_card (
    id BIGSERIAL PRIMARY KEY,
    review_material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_review_material(id) ON DELETE CASCADE,
    material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    source_key VARCHAR(100) NOT NULL,
    question VARCHAR(500) NOT NULL,
    answer TEXT NOT NULL,
    hint VARCHAR(300),
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    fsrs_card_json TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retrievability DOUBLE PRECISION NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    lapse_count INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_learning_review_card_source UNIQUE (material_id, source_key),
    CONSTRAINT ck_learning_review_retrievability CHECK (retrievability BETWEEN 0 AND 1),
    CONSTRAINT ck_learning_review_counts CHECK (review_count >= 0 AND lapse_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_learning_review_card_due
    ON learning_evidence.learning_review_card(user_id, active, due_at);

CREATE INDEX IF NOT EXISTS idx_learning_review_card_material
    ON learning_evidence.learning_review_card(material_id, active);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_log (
    id BIGSERIAL PRIMARY KEY,
    card_id BIGINT NOT NULL REFERENCES learning_evidence.learning_review_card(id) ON DELETE CASCADE,
    material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    rating SMALLINT NOT NULL,
    duration_ms INTEGER,
    reviewed_at TIMESTAMPTZ NOT NULL,
    previous_due_at TIMESTAMPTZ NOT NULL,
    next_due_at TIMESTAMPTZ NOT NULL,
    interval_days DOUBLE PRECISION NOT NULL,
    retrievability DOUBLE PRECISION NOT NULL,
    fsrs_review_log_json TEXT NOT NULL,
    state_rebuilt BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_learning_review_rating CHECK (rating BETWEEN 1 AND 4),
    CONSTRAINT ck_learning_review_duration CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT ck_learning_review_interval CHECK (interval_days >= 0),
    CONSTRAINT ck_learning_review_log_retrievability CHECK (retrievability BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS idx_learning_review_log_user_reviewed
    ON learning_evidence.learning_review_log(user_id, reviewed_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_review_log_card_reviewed
    ON learning_evidence.learning_review_log(card_id, reviewed_at DESC);
