CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
GRANT USAGE, CREATE ON SCHEMA learning_evidence TO postgres;
SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS video_slice (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    topic VARCHAR(255) NOT NULL,
    start_time VARCHAR(20) NOT NULL,
    end_time VARCHAR(20) NOT NULL,
    status VARCHAR(80) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_video_slice_title_start UNIQUE (title, start_time)
);

CREATE INDEX IF NOT EXISTS idx_video_slice_updated_at
    ON video_slice(updated_at DESC);

CREATE TABLE IF NOT EXISTS resume_evidence_alignment (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    requirement VARCHAR(255) NOT NULL,
    evidence TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_resume_evidence_user_requirement UNIQUE (user_id, requirement)
);

CREATE INDEX IF NOT EXISTS idx_resume_evidence_status
    ON resume_evidence_alignment(status);

CREATE INDEX IF NOT EXISTS idx_resume_evidence_user_updated
    ON resume_evidence_alignment(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jd_analysis_report (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    report_key VARCHAR(80) NOT NULL,
    job_description TEXT NOT NULL,
    match_score INTEGER NOT NULL DEFAULT 0,
    mastered_percent INTEGER NOT NULL DEFAULT 0,
    partial_percent INTEGER NOT NULL DEFAULT 0,
    gap_percent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_analysis_report_key UNIQUE (report_key)
);

CREATE INDEX IF NOT EXISTS idx_jd_analysis_report_user_updated
    ON jd_analysis_report(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jd_analysis_skill (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES jd_analysis_report(id) ON DELETE CASCADE,
    skill_name VARCHAR(160) NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_analysis_skill UNIQUE (report_id, skill_name)
);

CREATE TABLE IF NOT EXISTS jd_learning_plan_item (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES jd_analysis_report(id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_jd_learning_plan_item UNIQUE (report_id, step_no)
);

CREATE TABLE IF NOT EXISTS system_setting (
    setting_key VARCHAR(120) PRIMARY KEY,
    setting_group VARCHAR(80) NOT NULL,
    label VARCHAR(120) NOT NULL,
    setting_value VARCHAR(500) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
