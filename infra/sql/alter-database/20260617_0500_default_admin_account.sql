CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

-- 将旧默认管理员账号迁移为 admin；若已有 admin，则保留已有记录并刷新默认账号参数。
UPDATE learning_evidence.app_user
SET account = 'admin',
    email = 'admin@evidence.ai',
    display_name = '系统管理员',
    role = 'ADMIN',
    password_hash = '5d37928329dcaa2c3a5a2fe7dce62c3e7364bbe1d4a6dc5e6700ec968e0015a6',
    password_salt = 'learning-evidence-admin-salt-v1',
    password_algorithm = 'PBKDF2WithHmacSHA256',
    password_iterations = 120000,
    status = 'ACTIVE',
    updated_at = CURRENT_TIMESTAMP
WHERE account = 'admin@evidence.ai'
  AND NOT EXISTS (
      SELECT 1
      FROM learning_evidence.app_user
      WHERE account = 'admin'
  );

INSERT INTO learning_evidence.app_user (
    account,
    email,
    display_name,
    role,
    password_hash,
    password_salt,
    password_algorithm,
    password_iterations,
    status
)
VALUES (
    'admin',
    'admin@evidence.ai',
    '系统管理员',
    'ADMIN',
    '5d37928329dcaa2c3a5a2fe7dce62c3e7364bbe1d4a6dc5e6700ec968e0015a6',
    'learning-evidence-admin-salt-v1',
    'PBKDF2WithHmacSHA256',
    120000,
    'ACTIVE'
)
ON CONFLICT (account) DO UPDATE SET
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    password_hash = EXCLUDED.password_hash,
    password_salt = EXCLUDED.password_salt,
    password_algorithm = EXCLUDED.password_algorithm,
    password_iterations = EXCLUDED.password_iterations,
    status = EXCLUDED.status,
    updated_at = CURRENT_TIMESTAMP;
