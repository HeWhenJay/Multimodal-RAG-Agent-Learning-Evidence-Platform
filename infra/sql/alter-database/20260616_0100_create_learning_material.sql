CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS learning_material (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    user_id VARCHAR(120) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    status VARCHAR(30) NOT NULL,
    parser VARCHAR(80),
    document_summary TEXT,
    chunk_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 兼容已存在但未包含 user_id 的旧表，避免后续索引创建失败。
ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(120) NOT NULL DEFAULT 'legacy-user';

UPDATE learning_material
SET user_id = 'legacy-user'
WHERE user_id IS NULL;

ALTER TABLE learning_material
    ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE learning_material
    ALTER COLUMN user_id DROP DEFAULT;

CREATE INDEX IF NOT EXISTS idx_learning_material_status
    ON learning_material(status);

CREATE INDEX IF NOT EXISTS idx_learning_material_document_type
    ON learning_material(document_type);

CREATE INDEX IF NOT EXISTS idx_learning_material_user_updated
    ON learning_material(user_id, updated_at DESC);
