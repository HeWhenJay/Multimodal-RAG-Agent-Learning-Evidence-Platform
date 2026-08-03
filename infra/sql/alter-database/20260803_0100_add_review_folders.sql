-- 新增用户复习文件夹与文档归属；删除文件夹只解除归档，不删除资料和复习卡片。
CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_folder (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    name VARCHAR(80) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_learning_review_folder_name CHECK (LENGTH(BTRIM(name)) BETWEEN 1 AND 80),
    CONSTRAINT uk_learning_review_folder_user_name UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_learning_review_folder_user_updated
    ON learning_evidence.learning_review_folder(user_id, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_folder_material (
    material_id BIGINT PRIMARY KEY REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    folder_id BIGINT NOT NULL REFERENCES learning_evidence.learning_review_folder(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_review_folder_material_folder
    ON learning_evidence.learning_review_folder_material(folder_id, updated_at DESC, material_id);

CREATE INDEX IF NOT EXISTS idx_learning_review_folder_material_user
    ON learning_evidence.learning_review_folder_material(user_id, folder_id, material_id);
