-- 复习内容删除 tombstone：保留用户排除意图，避免同步或重新生成后复活。
CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_material_exclusion (
    material_id BIGINT PRIMARY KEY REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_review_material_exclusion_user
    ON learning_evidence.learning_review_material_exclusion(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_card_exclusion (
    id BIGSERIAL PRIMARY KEY,
    original_card_id BIGINT NOT NULL,
    material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    source_key VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_learning_review_card_exclusion_original UNIQUE (user_id, original_card_id),
    CONSTRAINT uk_learning_review_card_exclusion_source UNIQUE (material_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_learning_review_card_exclusion_user
    ON learning_evidence.learning_review_card_exclusion(user_id, created_at DESC);
