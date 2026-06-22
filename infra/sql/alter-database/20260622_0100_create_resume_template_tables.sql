CREATE TABLE IF NOT EXISTS learning_evidence.resume_template (
    id VARCHAR(120) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    template_name VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    original_file_path VARCHAR(700) NOT NULL,
    storage_type VARCHAR(30) NOT NULL DEFAULT 'local',
    object_key VARCHAR(700),
    public_url VARCHAR(700),
    current_filename VARCHAR(255),
    current_file_path VARCHAR(700),
    current_storage_type VARCHAR(30),
    current_object_key VARCHAR(700),
    current_public_url VARCHAR(700),
    file_type VARCHAR(20) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(30) NOT NULL DEFAULT 'PARSING',
    layout_fingerprint_json TEXT NOT NULL DEFAULT '{}',
    unsupported_regions_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resume_template_user_updated
    ON learning_evidence.resume_template(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_resume_template_status
    ON learning_evidence.resume_template(status);

CREATE TABLE IF NOT EXISTS learning_evidence.resume_template_field (
    id VARCHAR(120) PRIMARY KEY,
    template_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    template_version INTEGER NOT NULL,
    field_id VARCHAR(120) NOT NULL,
    section_key VARCHAR(60) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    source_text TEXT NOT NULL,
    source_text_hash VARCHAR(128) NOT NULL,
    location_refs_json TEXT NOT NULL DEFAULT '[]',
    style_fingerprint_json TEXT NOT NULL DEFAULT '{}',
    max_chars INTEGER NOT NULL,
    max_lines INTEGER NOT NULL,
    required_evidence_policy VARCHAR(30) NOT NULL,
    unsupported_regions_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_resume_template_field UNIQUE (template_id, template_version, field_id)
);

CREATE INDEX IF NOT EXISTS idx_resume_template_field_template
    ON learning_evidence.resume_template_field(template_id, template_version);

CREATE TABLE IF NOT EXISTS learning_evidence.resume_template_patch_draft (
    id VARCHAR(120) PRIMARY KEY,
    template_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    template_version INTEGER NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
    job_description_hash VARCHAR(128) NOT NULL,
    patches_json TEXT NOT NULL DEFAULT '[]',
    evidence_candidates_json TEXT NOT NULL DEFAULT '[]',
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    provider VARCHAR(40),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resume_patch_draft_template
    ON learning_evidence.resume_template_patch_draft(template_id, template_version, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_resume_patch_draft_status
    ON learning_evidence.resume_template_patch_draft(status);

CREATE TABLE IF NOT EXISTS learning_evidence.resume_template_export (
    id VARCHAR(120) PRIMARY KEY,
    template_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    base_version INTEGER NOT NULL,
    export_version INTEGER NOT NULL,
    patch_draft_id VARCHAR(120) NOT NULL REFERENCES learning_evidence.resume_template_patch_draft(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(700) NOT NULL,
    storage_type VARCHAR(30) NOT NULL,
    object_key VARCHAR(700),
    public_url VARCHAR(700),
    layout_validation_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key VARCHAR(160) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'EXPORTED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_resume_template_export_idempotency UNIQUE (template_id, user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_resume_template_export_template
    ON learning_evidence.resume_template_export(template_id, export_version DESC);
