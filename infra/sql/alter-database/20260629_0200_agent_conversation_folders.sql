-- 新增 Agent 会话文件夹，并允许 agent_task 归类到用户自定义文件夹。
CREATE TABLE IF NOT EXISTS learning_evidence.agent_conversation_folder (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    name VARCHAR(80) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE learning_evidence.agent_task
    ADD COLUMN IF NOT EXISTS folder_id VARCHAR(120);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_task_folder'
    ) THEN
        ALTER TABLE learning_evidence.agent_task
            ADD CONSTRAINT fk_agent_task_folder
            FOREIGN KEY (folder_id)
            REFERENCES learning_evidence.agent_conversation_folder(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_conversation_folder_user_sort
    ON learning_evidence.agent_conversation_folder(user_id, sort_order, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_task_user_folder_updated
    ON learning_evidence.agent_task(user_id, folder_id, updated_at DESC);
