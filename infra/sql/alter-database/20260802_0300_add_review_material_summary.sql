-- 为复习资料保存提炼摘要；原资料自带摘要仍由查询层优先返回。
ALTER TABLE learning_evidence.learning_review_material
    ADD COLUMN IF NOT EXISTS summary TEXT;
