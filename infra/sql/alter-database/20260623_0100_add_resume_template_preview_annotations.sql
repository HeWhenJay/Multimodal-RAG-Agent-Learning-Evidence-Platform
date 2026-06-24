ALTER TABLE learning_evidence.resume_template_patch_draft
    ADD COLUMN IF NOT EXISTS allowed_field_ids_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE learning_evidence.resume_template_patch_draft
    ADD COLUMN IF NOT EXISTS annotation_revision INTEGER;

CREATE TABLE IF NOT EXISTS learning_evidence.resume_template_preview_page (
    id VARCHAR(120) PRIMARY KEY,
    template_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    template_version INTEGER NOT NULL,
    page_index INTEGER NOT NULL,
    storage_type VARCHAR(30) NOT NULL,
    file_path VARCHAR(700),
    object_key VARCHAR(700),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_resume_template_preview_page UNIQUE (template_id, template_version, page_index)
);

CREATE INDEX IF NOT EXISTS idx_resume_template_preview_page_user
    ON learning_evidence.resume_template_preview_page(user_id, template_id, template_version);

CREATE TABLE IF NOT EXISTS learning_evidence.resume_template_region_annotation (
    id VARCHAR(120) PRIMARY KEY,
    template_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    template_version INTEGER NOT NULL,
    field_id VARCHAR(120),
    page_index INTEGER NOT NULL,
    rect_json TEXT NOT NULL,
    source_type VARCHAR(30) NOT NULL CHECK (source_type IN ('AUTO','MANUAL_BOUND','MANUAL_UNBOUND')),
    editable BOOLEAN NOT NULL DEFAULT FALSE,
    section_key VARCHAR(60) NOT NULL,
    user_instruction VARCHAR(500),
    required_evidence_policy VARCHAR(30) NOT NULL CHECK (required_evidence_policy IN ('NONE','OPTIONAL','REQUIRED')),
    status VARCHAR(30) NOT NULL CHECK (status IN ('ACTIVE','IGNORED')),
    annotation_revision INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resume_template_region_annotation_status
    ON learning_evidence.resume_template_region_annotation(user_id, template_id, template_version, status);

CREATE INDEX IF NOT EXISTS idx_resume_template_region_annotation_field
    ON learning_evidence.resume_template_region_annotation(template_id, template_version, field_id);
