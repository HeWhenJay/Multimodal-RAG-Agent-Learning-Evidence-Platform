-- 当前项目主动同步 DSH 插件本地 v2 知识库；插件本身不依赖本项目。
CREATE TABLE IF NOT EXISTS learning_evidence.dsh_local_material_sync (
    id BIGSERIAL PRIMARY KEY,
    project_user_id VARCHAR(120) NOT NULL,
    dsh_document_id VARCHAR(160) NOT NULL,
    content_hash CHAR(64) NOT NULL,
    project_material_id BIGINT NOT NULL REFERENCES learning_evidence.learning_material(id) ON DELETE CASCADE,
    plugin_summary TEXT,
    plugin_system_category VARCHAR(80),
    plugin_user_category VARCHAR(80),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_dsh_local_material_sync_user_document UNIQUE (project_user_id, dsh_document_id)
);

CREATE INDEX IF NOT EXISTS idx_dsh_local_material_sync_user_updated
    ON learning_evidence.dsh_local_material_sync(project_user_id, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS learning_evidence.dsh_local_sync_owner (
    singleton_key SMALLINT PRIMARY KEY DEFAULT 1,
    project_user_id VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_dsh_local_sync_owner_singleton CHECK (singleton_key = 1)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM learning_evidence.learning_material
        WHERE LEFT(COALESCE(source, ''), 10) = 'dsh-local:'
        GROUP BY user_id, source
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION '发现重复 DSH 本地同步来源，请先合并重复 learning_material 记录后重试迁移';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_material_user_dsh_local_source
    ON learning_evidence.learning_material(user_id, source)
    WHERE LEFT(COALESCE(source, ''), 10) = 'dsh-local:';
