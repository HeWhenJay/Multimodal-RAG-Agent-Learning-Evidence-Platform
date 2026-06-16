# PostgreSQL/pgvector 知识仓库创建语句

以下 SQL 面向真实 PostgreSQL + pgvector 环境。`learning_evidence` 是项目数据库，`rag_document` 保存资料级索引，`rag_chunk` 保存递归切块、元数据、词频统计和 pgvector 向量。

## 1. 创建数据库和账号

使用 PostgreSQL 超级用户执行：

```sql
CREATE DATABASE learning_evidence
    WITH
    ENCODING = 'UTF8'
    TEMPLATE = template0;

CREATE USER learning_evidence_app WITH PASSWORD 'learning_evidence_app';

GRANT ALL PRIVILEGES ON DATABASE learning_evidence TO learning_evidence_app;
```

进入项目数据库后执行：

```sql
\connect learning_evidence

CREATE EXTENSION IF NOT EXISTS vector;

GRANT USAGE, CREATE ON SCHEMA public TO learning_evidence_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO learning_evidence_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO learning_evidence_app;
```

## 2. 创建业务资料表

```sql
CREATE TABLE IF NOT EXISTS learning_material (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    status VARCHAR(30) NOT NULL,
    parser VARCHAR(80),
    document_summary TEXT,
    chunk_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_material_status
    ON learning_material(status);

CREATE INDEX IF NOT EXISTS idx_learning_material_document_type
    ON learning_material(document_type);
```

## 3. 创建 RAG 向量仓库表

```sql
CREATE TABLE IF NOT EXISTS rag_document (
    document_id VARCHAR(120) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    user_id VARCHAR(120) NOT NULL DEFAULT 'demo-user',
    visibility_scope VARCHAR(30) NOT NULL DEFAULT 'private',
    language VARCHAR(30) NOT NULL DEFAULT 'zh-CN',
    parser VARCHAR(80),
    document_summary TEXT,
    section_summaries JSONB NOT NULL DEFAULT '{}'::jsonb,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rag_chunk (
    chunk_id VARCHAR(180) PRIMARY KEY,
    document_id VARCHAR(120) NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    chunk_position INTEGER NOT NULL,
    section_name VARCHAR(255) NOT NULL DEFAULT '全文',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    term_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding VECTOR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_document_type
    ON rag_document(document_type);

CREATE INDEX IF NOT EXISTS idx_rag_document_user_visibility
    ON rag_document(user_id, visibility_scope);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_document_position
    ON rag_chunk(document_id, chunk_position);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_metadata_gin
    ON rag_chunk USING GIN (metadata);

CREATE INDEX IF NOT EXISTS idx_rag_chunk_embedding_hnsw
    ON rag_chunk USING hnsw (embedding vector_cosine_ops);
```

## 4. Python RAG 连接配置

```powershell
$env:RAG_STORE_BACKEND='pgvector'
$env:RAG_DATABASE_URL='postgresql://learning_evidence_app:learning_evidence_app@127.0.0.1:5432/learning_evidence'
$env:RAG_VECTOR_DIMENSIONS='128'
```

未配置 `RAG_DATABASE_URL` 时，Python 服务会使用内存后端，方便无数据库环境下跑单元测试；正式运行必须使用以上 pgvector 配置。
