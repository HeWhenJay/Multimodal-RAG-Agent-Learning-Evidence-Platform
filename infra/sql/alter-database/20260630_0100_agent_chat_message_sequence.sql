-- 为 Agent 聊天消息增加任务内稳定顺序号，避免同一 created_at 下随机 UUID 影响上下文窗口排序。
ALTER TABLE learning_evidence.agent_chat_message
    ADD COLUMN IF NOT EXISTS sequence_no BIGINT NOT NULL DEFAULT 0;

WITH ordered_messages AS (
    SELECT
        id,
        task_id,
        ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY created_at ASC, id ASC) AS rn
    FROM learning_evidence.agent_chat_message
    WHERE task_id IN (
        SELECT task_id
        FROM learning_evidence.agent_chat_message
        GROUP BY task_id
        HAVING COUNT(*) FILTER (WHERE sequence_no = 0) > 0
            OR COUNT(*) <> COUNT(DISTINCT sequence_no)
    )
)
UPDATE learning_evidence.agent_chat_message message
SET sequence_no = ordered_messages.rn
FROM ordered_messages
WHERE message.task_id = ordered_messages.task_id
  AND message.id = ordered_messages.id;

DROP INDEX IF EXISTS learning_evidence.idx_agent_chat_message_task_sequence;

CREATE UNIQUE INDEX IF NOT EXISTS uk_agent_chat_message_task_sequence
    ON learning_evidence.agent_chat_message(task_id, sequence_no);
