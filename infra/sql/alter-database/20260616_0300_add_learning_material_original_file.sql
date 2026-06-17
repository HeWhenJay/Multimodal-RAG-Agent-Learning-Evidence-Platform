CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS original_file_path VARCHAR(500);
