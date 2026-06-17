CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(120) NOT NULL DEFAULT 'legacy-user';

UPDATE learning_material
SET user_id = 'legacy-user'
WHERE user_id IS NULL;

ALTER TABLE learning_material
    ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE learning_material
    ALTER COLUMN user_id DROP DEFAULT;

CREATE INDEX IF NOT EXISTS idx_learning_material_user_updated
    ON learning_material(user_id, updated_at DESC);
