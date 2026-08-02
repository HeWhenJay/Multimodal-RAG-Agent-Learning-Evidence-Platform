-- 按用户和复习资料保存今日分组拖拽顺序，未排序资料允许为空并由查询层稳定追加。
ALTER TABLE learning_evidence.learning_review_material
    ADD COLUMN IF NOT EXISTS display_order INTEGER;

CREATE INDEX IF NOT EXISTS idx_learning_review_material_user_order
    ON learning_evidence.learning_review_material(user_id, display_order, material_id)
    WHERE is_learning_content IS TRUE AND status = 'GENERATED';
