ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);

ALTER TABLE learning_material
    ADD COLUMN IF NOT EXISTS original_file_path VARCHAR(500);
