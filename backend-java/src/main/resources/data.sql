MERGE INTO app_user (
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
KEY(account)
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
);
