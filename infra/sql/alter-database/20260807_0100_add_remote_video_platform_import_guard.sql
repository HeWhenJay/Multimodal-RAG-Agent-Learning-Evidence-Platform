-- 将仅覆盖 Bilibili 的远程资料唯一索引升级为 Bilibili/抖音共用约束。
-- init.sql 已使用最终索引；本迁移负责把已有数据库从旧索引前向迁移到相同状态。
DROP INDEX IF EXISTS learning_evidence.uq_learning_material_user_bilibili_url;
DROP INDEX IF EXISTS learning_evidence.uq_learning_material_user_douyin_url;

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_material_user_remote_url
    ON learning_evidence.learning_material(user_id, public_url)
    WHERE storage_type = 'remote'
      AND source IN ('bilibili', 'douyin')
      AND public_url IS NOT NULL;
