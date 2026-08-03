-- 持久化复习生成当前阶段与最近事件，让长时间 DeepSeek 调用可以被前端轮询观察。
ALTER TABLE learning_evidence.learning_review_material
    ADD COLUMN IF NOT EXISTS generation_progress JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE learning_evidence.learning_review_material
SET generation_progress = '{}'::jsonb
WHERE generation_progress IS NULL;
