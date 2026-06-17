-- 增加上传文件对象存储元数据，支持阿里 OSS 来源追踪和视频播放定位
ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS storage_type VARCHAR(30) NOT NULL DEFAULT 'local';

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS object_key VARCHAR(700);

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS public_url VARCHAR(700);
