-- 防止同一用户重复创建相同 Bilibili 远程资料。
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_material_user_bilibili_url
    ON learning_evidence.learning_material(user_id, public_url)
    WHERE storage_type = 'remote'
      AND source = 'bilibili'
      AND public_url IS NOT NULL;
