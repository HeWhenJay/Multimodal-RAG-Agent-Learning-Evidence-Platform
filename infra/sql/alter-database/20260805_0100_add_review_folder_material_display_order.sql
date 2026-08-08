-- 为复习文件夹内文档增加独立顺序，旧归档按原列表顺序回填后可继续拖拽调整。
ALTER TABLE learning_evidence.learning_review_folder_material
    ADD COLUMN IF NOT EXISTS display_order INTEGER;

WITH ranked_materials AS (
    SELECT
        material_id,
        ROW_NUMBER() OVER (
            PARTITION BY folder_id, user_id
            ORDER BY updated_at DESC, material_id DESC
        ) - 1 AS position
    FROM learning_evidence.learning_review_folder_material
)
UPDATE learning_evidence.learning_review_folder_material target
SET display_order = ranked.position
FROM ranked_materials ranked
WHERE target.material_id = ranked.material_id
  AND target.display_order IS NULL;

CREATE INDEX IF NOT EXISTS idx_learning_review_folder_material_order
    ON learning_evidence.learning_review_folder_material(folder_id, user_id, display_order, material_id);
