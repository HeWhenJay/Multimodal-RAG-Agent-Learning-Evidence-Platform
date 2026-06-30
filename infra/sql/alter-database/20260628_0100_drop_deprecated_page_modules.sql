-- Drop deprecated standalone page modules now handled by Agent workflows.
DROP TABLE IF EXISTS learning_evidence.resume_template_export CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_template_patch_draft CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_template_region_annotation CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_template_preview_page CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_template_field CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_template CASCADE;
DROP TABLE IF EXISTS learning_evidence.jd_learning_plan_item CASCADE;
DROP TABLE IF EXISTS learning_evidence.jd_analysis_skill CASCADE;
DROP TABLE IF EXISTS learning_evidence.jd_analysis_report CASCADE;
DROP TABLE IF EXISTS learning_evidence.resume_evidence_alignment CASCADE;
DROP TABLE IF EXISTS learning_evidence.video_slice CASCADE;
