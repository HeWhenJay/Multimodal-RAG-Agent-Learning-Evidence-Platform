CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres;
SET search_path TO learning_evidence, public;

CREATE TABLE IF NOT EXISTS app_user (
    id BIGSERIAL PRIMARY KEY,
    account VARCHAR(120) NOT NULL,
    email VARCHAR(160),
    display_name VARCHAR(80) NOT NULL,
    role VARCHAR(40) NOT NULL DEFAULT 'ADMIN',
    password_hash VARCHAR(128) NOT NULL,
    password_salt VARCHAR(128) NOT NULL,
    password_algorithm VARCHAR(40) NOT NULL DEFAULT 'PBKDF2WithHmacSHA256',
    password_iterations INTEGER NOT NULL DEFAULT 120000,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    last_login_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_app_user_account UNIQUE (account)
);

CREATE INDEX IF NOT EXISTS idx_app_user_status
    ON app_user(status);

INSERT INTO app_user (
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
    'admin@evidence.ai',
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

CREATE TABLE IF NOT EXISTS auth_session (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    token_hash VARCHAR(128) NOT NULL,
    remember_me BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_auth_session_token_hash UNIQUE (token_hash)
);

CREATE INDEX IF NOT EXISTS idx_auth_session_user_id
    ON auth_session(user_id);

CREATE INDEX IF NOT EXISTS idx_auth_session_expires_at
    ON auth_session(expires_at);

CREATE TABLE IF NOT EXISTS auth_login_record (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES app_user(id) ON DELETE SET NULL,
    account VARCHAR(120) NOT NULL,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255),
    ip_address VARCHAR(80),
    user_agent VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auth_login_record_account
    ON auth_login_record(account);

CREATE INDEX IF NOT EXISTS idx_auth_login_record_created_at
    ON auth_login_record(created_at DESC);

ALTER TABLE app_user
    ALTER COLUMN last_login_at TYPE TIMESTAMP USING last_login_at AT TIME ZONE current_setting('TIMEZONE'),
    ALTER COLUMN created_at TYPE TIMESTAMP USING created_at AT TIME ZONE current_setting('TIMEZONE'),
    ALTER COLUMN updated_at TYPE TIMESTAMP USING updated_at AT TIME ZONE current_setting('TIMEZONE');

ALTER TABLE auth_session
    ALTER COLUMN expires_at TYPE TIMESTAMP USING expires_at AT TIME ZONE current_setting('TIMEZONE'),
    ALTER COLUMN created_at TYPE TIMESTAMP USING created_at AT TIME ZONE current_setting('TIMEZONE'),
    ALTER COLUMN updated_at TYPE TIMESTAMP USING updated_at AT TIME ZONE current_setting('TIMEZONE');

ALTER TABLE auth_login_record
    ALTER COLUMN created_at TYPE TIMESTAMP USING created_at AT TIME ZONE current_setting('TIMEZONE');
