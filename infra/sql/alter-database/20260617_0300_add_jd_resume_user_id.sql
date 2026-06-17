CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

ALTER TABLE jd_analysis_report
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(120) NOT NULL DEFAULT 'legacy-user';

ALTER TABLE jd_analysis_report
    ALTER COLUMN user_id DROP DEFAULT;

CREATE INDEX IF NOT EXISTS idx_jd_analysis_report_user_updated
    ON jd_analysis_report(user_id, updated_at DESC);

ALTER TABLE resume_evidence_alignment
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(120) NOT NULL DEFAULT 'legacy-user';

ALTER TABLE resume_evidence_alignment
    ALTER COLUMN user_id DROP DEFAULT;

ALTER TABLE resume_evidence_alignment
    DROP CONSTRAINT IF EXISTS uk_resume_evidence_requirement;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uk_resume_evidence_user_requirement'
          AND conrelid = 'resume_evidence_alignment'::regclass
    ) THEN
        ALTER TABLE resume_evidence_alignment
            ADD CONSTRAINT uk_resume_evidence_user_requirement UNIQUE (user_id, requirement);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_resume_evidence_user_updated
    ON resume_evidence_alignment(user_id, updated_at DESC);
