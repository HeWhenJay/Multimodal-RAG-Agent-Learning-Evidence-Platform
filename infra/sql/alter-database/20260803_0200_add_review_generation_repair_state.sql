-- 复习卡片多轮质量修复与人工处理终态，所有操作均可重复执行。
ALTER TABLE learning_evidence.learning_review_material
    ADD COLUMN IF NOT EXISTS generation_attempts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE learning_evidence.learning_review_material
    ADD COLUMN IF NOT EXISTS quality_feedback JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE learning_evidence.learning_review_material
SET quality_feedback = '[]'::jsonb
WHERE quality_feedback IS NULL;

ALTER TABLE learning_evidence.learning_review_material
    DROP CONSTRAINT IF EXISTS ck_learning_review_material_status;

ALTER TABLE learning_evidence.learning_review_material
    ADD CONSTRAINT ck_learning_review_material_status
    CHECK (status IN ('PENDING', 'GENERATING', 'GENERATED', 'SKIPPED', 'FAILED', 'NEEDS_REVIEW'));
