-- 允许用户在卡片提示中保留更完整的 Markdown 结构。
ALTER TABLE learning_evidence.learning_review_card
    ALTER COLUMN hint TYPE TEXT;
