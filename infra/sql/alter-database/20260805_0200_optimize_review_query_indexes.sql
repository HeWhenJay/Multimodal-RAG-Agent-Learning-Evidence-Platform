-- 为复习中心高频筛选、到期队列和文件夹详情补充联合索引，减少过滤后的排序与回表。
CREATE INDEX IF NOT EXISTS idx_learning_material_user_status_updated_id
    ON learning_evidence.learning_material(user_id, status, updated_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_learning_review_material_user_status_version_material
    ON learning_evidence.learning_review_material(user_id, status, index_request_version, material_id);

CREATE INDEX IF NOT EXISTS idx_learning_review_card_user_active_due_material_id
    ON learning_evidence.learning_review_card(user_id, active, due_at, material_id, id);

CREATE INDEX IF NOT EXISTS idx_learning_review_card_material_user_active_id
    ON learning_evidence.learning_review_card(material_id, user_id, active, id);

CREATE INDEX IF NOT EXISTS idx_learning_review_log_user_reviewed_material
    ON learning_evidence.learning_review_log(user_id, reviewed_at, material_id);
